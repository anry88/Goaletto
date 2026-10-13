"""Read-only diagnostics over the single existing Goaletto database.

No reset, no writes, no ownership effects, no network calls. Execution is left
to Codex sequentially after the world is saved; do not run against another
project database. New local/CI databases are forbidden, so this module never
creates one and never uses the destructive ``sessions`` fixture.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text

from goaletto.config import Settings
from goaletto.diagnostics.checks import FINDING_CODES, collect_report
from goaletto.diagnostics.queries import collect_snapshot
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.models import Job, Season

pytestmark = pytest.mark.integration


async def test_diagnostics_snapshot_is_read_only_bounded_and_secret_free() -> None:
    database = Database(Settings())
    try:
        async with database.read_connection() as connection:
            assert await connection.scalar(text("SHOW transaction_read_only")) == "on"
            isolation = await connection.scalar(text("SHOW transaction_isolation"))
            assert isolation == "repeatable read"
            before = await connection.scalar(select(func.count()).select_from(Job))
            snapshot = await collect_snapshot(connection, now=datetime.now(UTC), limit=10)
            after = await connection.scalar(select(func.count()).select_from(Job))
            assert after == before
            # Bounded scopes report their own truncation; nothing is silently cut.
            assert ("jobs" in snapshot.incomplete_scopes) == snapshot.jobs_truncated
            assert ("recoveries" in snapshot.incomplete_scopes) == snapshot.recoveries_truncated
            assert ("finance_accounts" in snapshot.incomplete_scopes) == snapshot.finance_truncated
            assert ("reservations" in snapshot.incomplete_scopes) == (
                snapshot.reservations_truncated
            )
            assert ("auctions" in snapshot.incomplete_scopes) == snapshot.auctions_truncated
            assert ("bids" in snapshot.incomplete_scopes) == snapshot.bids_truncated
        report = collect_report(snapshot, late_threshold_seconds=180.0, row_limit=10)
        assert report.now.tzinfo is not None
        for finding in report.findings:
            assert finding.code in FINDING_CODES
        for key in (
            "recoveries_total",
            "finance_accounts_total",
            "reservations_total",
            "auctions_total",
            "bids_total",
        ):
            assert key in report.aggregates
        import json

        blob = json.dumps(
            {
                "findings": [
                    {"code": finding.code, "severity": finding.severity}
                    for finding in report.findings
                ],
                "aggregates": report.aggregates,
            }
        ).lower()
        for secret in ("token", "dsn", "password", "traceback", "exception"):
            assert secret not in blob
    finally:
        await database.close()


async def test_diagnostics_default_limit_reads_current_world() -> None:
    database = Database(Settings())
    try:
        async with database.read_connection() as connection:
            snapshot = await collect_snapshot(connection, now=datetime.now(UTC), limit=200)
            assert snapshot.market_schedule_complete
            season_id = await connection.scalar(select(Season.id).limit(1))
            if season_id is not None:
                scoped = await collect_snapshot(
                    connection, now=datetime.now(UTC), season_id=season_id, limit=200
                )
                assert not scoped.market_schedule_complete
                codes = {f.code for f in collect_report(scoped).findings}
                assert "MARKET_SCHEDULE_SCOPE_LIMITED" in codes
                assert "MARKET_CLOSE_JOB_MISSING" not in codes
        report = collect_report(snapshot, late_threshold_seconds=180.0, row_limit=200)
        assert report.late_threshold_seconds == 180.0
        assert report.truncated == bool(snapshot.incomplete_scopes)
    finally:
        await database.close()


async def test_recovery_projection_validates_committed_hash(sessions):
    from datetime import timedelta

    from goaletto.application.match_runtime import handle_calendar_job
    from tests.integration.test_agent_wave import job, setup_world, start_fixture

    _, _, club_id = await setup_world(sessions)
    fixture_id, _ = await start_fixture(sessions, club_id)
    await job(sessions, "match.tick", fixture_id, 1)
    async with sessions.begin() as session:
        work = await session.scalar(
            select(Job).where(
                Job.kind == "match.tick",
                Job.payload["fixture_id"].astext == str(fixture_id),
                Job.payload["tick_number"].as_integer() == 2,
            )
        )
        at = work.due_at + timedelta(minutes=5)
        assert await handle_calendar_job(session, work, at) == "rescheduled"
    database = Database(Settings())
    try:
        async with database.read_connection() as connection:
            snapshot = await collect_snapshot(connection, now=at, limit=20000)
        assert len(snapshot.recoveries) == 1
        assert snapshot.recoveries[0].state_hash in snapshot.recoveries[0].checkpoint_hashes
        report = collect_report(snapshot)
        assert "MATCH_RECOVERY_MISMATCH" not in {f.code for f in report.findings}
        async with sessions.begin() as session:
            await session.execute(
                text("UPDATE match_recoveries SET state_hash=:hash"), {"hash": "f" * 64}
            )
        async with database.read_connection() as connection:
            broken = await collect_snapshot(connection, now=at, limit=20000)
        assert "MATCH_RECOVERY_MISMATCH" in {f.code for f in collect_report(broken).findings}
    finally:
        await database.close()
