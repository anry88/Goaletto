"""Actual least-privilege login and Grafana SQL parity on the one Goaletto DB."""

import json
from datetime import timedelta
from pathlib import Path
from secrets import token_hex
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from goaletto.config import Settings
from goaletto.infrastructure.acquisition_queries import source_funnel
from goaletto.infrastructure.analytics_reports_queries import entry_funnel, retention
from goaletto.infrastructure.models import AnalyticsCoverage, AnalyticsExclusion, OutboxMessage
from goaletto.infrastructure.onboarding_queries import launch_report
from tests.integration.test_analytics_reports_pg import (
    COHORT_FROM,
    COVERAGE_AT,
    START,
    T,
    _event,
    _manager,
    _session,
    _start,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture
async def reader(sessions):
    name, password = f"goaletto_grafana_check_{uuid4().hex[:12]}", token_hex(32)
    async with sessions.begin() as session:
        connection = await session.connection()
        raw = await connection.get_raw_connection()
        await raw.driver_connection.execute((ROOT / "deploy/grafana/access.sql").read_text())
        try:
            await raw.driver_connection.execute(
                f"CREATE ROLE \"{name}\" LOGIN PASSWORD '{password}' INHERIT "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            )
            await session.execute(text(f'GRANT goaletto_operator_reader TO "{name}"'))
        except Exception as error:
            raise RuntimeError(f"Operator login setup failed ({type(error).__name__})") from None
    url = make_url(Settings().database_url.get_secret_value()).set(username=name, password=password)
    engine = create_async_engine(url, hide_parameters=True)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        async with sessions.begin() as session:
            await session.execute(text(f'DROP ROLE "{name}"'))


async def sql_report(reader, report, filters=None, *, start=COHORT_FROM, at=T):
    async with reader.begin() as session:
        result = await session.scalar(
            text("SELECT goaletto_operator.report(:report,:start,:at,CAST(:filters AS jsonb))"),
            {"report": report, "start": start, "at": at, "filters": json.dumps(filters or {})},
        )
    return json.loads(result) if isinstance(result, str) else result


async def test_real_login_matches_cli_and_filters_mature_external_cohort(sessions, reader):
    async with sessions.begin() as session:
        session.add(AnalyticsCoverage(version=1, started_at=COVERAGE_AT, retention_days=180))
        people = []
        for i in range(5):
            manager, mapping = await _manager(session, 8000 + i, internal=i == 4)
            manager.locale = "en" if i == 1 else "ru"
            await _start(session, mapping, START + timedelta(hours=1) if i == 3 else START)
            people.append((manager, mapping))
        # Opt-out is still excluded even when there are residual synthetic rows.
        session.add(AnalyticsExclusion(manager_id=people[2][0].id, kind="opt_out", excluded_at=T))
        for index in (0, 4):
            mapping = people[index][1]
            row = await _session(session, mapping, START)
            ownership = uuid4()
            await _event(
                session,
                mapping,
                row,
                "club_claimed",
                "user",
                START,
                START,
                ownership=ownership,
                user_activity=True,
            )
            await _event(
                session,
                mapping,
                row,
                "lineup_saved",
                "user",
                START,
                START,
                ownership=ownership,
                user_activity=True,
            )
    observed = await sql_report(reader, "entry")
    row = observed["rows"][0]
    assert (row["starts"], row["eligible"], row["waiting"], row["claimed"], row["ready"]) == (
        3,
        2,
        1,
        1,
        1,
    )
    async with sessions.begin() as session:
        expected = await entry_funnel(session, COHORT_FROM, T)
        assert all(row[k] == getattr(expected, k) for k in row)
        expected_sources = await source_funnel(session, COHORT_FROM, T)
        expected_retention = await retention(session, COHORT_FROM, T)
        expected_launch = {
            name: await launch_report(session, COHORT_FROM, T, report=name)
            for name in ("last-steps", "recovery", "match-opportunities")
        }
    assert (await sql_report(reader, "sources"))["rows"] == list(expected_sources.rows)
    retained = (await sql_report(reader, "retention"))["rows"]
    assert [
        (r["day"], r["eligible"], r["waiting"], r["retained"], r["unknown_late"]) for r in retained
    ] == [
        (r.day, r.eligible_N, r.waiting, r.retained_n, r.unknown_late)
        for r in expected_retention.rows
    ]
    for name, expected in expected_launch.items():
        actual = (await sql_report(reader, name.replace("-", "_").replace("match_", "")))["rows"]
        # JSON serializes date/UUID dimensions; compare their canonical representations.
        assert json.dumps(actual, sort_keys=True, default=str) == json.dumps(
            expected["rows"], sort_keys=True, default=str
        )
    en = (await sql_report(reader, "entry", {"locale": "en"}))["rows"][0]
    assert (en["starts"], en["eligible"], en["ready"]) == (1, 1, 0)
    assert (await sql_report(reader, "entry", {"source": "known"}))["rows"][0]["starts"] == 0
    # No actual exposure -> no fabricated experiment control population.
    assert (await sql_report(reader, "entry", {"variant": "control"}))["rows"][0]["starts"] == 0
    clipped = await sql_report(reader, "entry", start=COVERAGE_AT - timedelta(days=1))
    assert clipped["coverage_limited"] is True
    earlier = await sql_report(
        reader, "entry", start=COVERAGE_AT - timedelta(days=2), at=COVERAGE_AT - timedelta(days=1)
    )
    assert earlier["state"] == "outside_coverage" and earlier["rows"] == []


async def test_reader_cannot_read_people_comments_write_or_hijack_search_path(sessions, reader):
    assert (await sql_report(reader, "entry"))["state"] == "coverage_not_started"
    assert (await sql_report(reader, "entry"))["rows"] == []
    for statement in (
        "SELECT * FROM public.managers",
        "SELECT * FROM public.feedback_responses",
        "SELECT * FROM public.feedback_comments",
        "SELECT * FROM public.journey_events",
        "SELECT * FROM public.outbox_messages",
        "UPDATE public.clubs SET budget=0",
        "TRUNCATE public.matches CASCADE",
        "CREATE TABLE public.operator_intrusion(id integer)",
        "CREATE TABLE goaletto_operator.operator_intrusion(id integer)",
        "SET ROLE goaletto",
    ):
        async with reader() as session:
            with pytest.raises(DBAPIError):
                await session.execute(text(statement))
            await session.rollback()
    async with reader.begin() as session:
        # The login may create its own temporary tables, but cannot shadow the function's sources.
        await session.execute(text("CREATE TEMP TABLE analytics_coverage(version integer)"))
        await session.execute(text("SET LOCAL search_path = pg_temp, public"))
        result = await session.scalar(
            text("SELECT goaletto_operator.report('entry',:start,:at,'{}'::jsonb)"),
            {"start": COHORT_FROM, "at": T},
        )
        if isinstance(result, str):
            result = json.loads(result)
        assert result["state"] == "coverage_not_started"
    for report, filters in [
        ("entry", {"locale": "ru'; DELETE FROM managers;--"}),
        ("delivery", {"source": "known"}),
        ("unknown", {}),
    ]:
        with pytest.raises(DBAPIError):
            await sql_report(reader, report, filters)


async def test_every_dashboard_query_executes_as_real_reader_and_keeps_unknown(sessions, reader):
    async with sessions.begin() as session:
        session.add(AnalyticsCoverage(version=1, started_at=COVERAGE_AT, retention_days=180))
        session.add(
            OutboxMessage(
                id=uuid4(),
                dedupe_key="operator-unknown",
                chat_id=42,
                payload={"kind": "match_result"},
                due_at=START,
                status="failed",
                attempts=1,
                last_error="unknown_outcome",
            )
        )
    sent = (await sql_report(reader, "delivery"))["rows"][0]
    assert sent["delivered"] == 0 and sent["failed_unknown"] == 1 and sent["unknown_messages"] == 1
    # Execute the literal deployed dashboard SQL after only Grafana macro substitution.
    for path in (ROOT / "deploy/grafana/dashboards").glob("*.json"):
        dashboard = json.loads(path.read_text())
        queries = [t["rawSql"] for p in dashboard["panels"] for t in p.get("targets", [])]
        queries += [v["query"] for v in dashboard["templating"]["list"] if v["type"] == "query"]
        for query in queries:
            query = query.replace("$__timeFrom()", "'2026-10-11T00:00:00Z'").replace(
                "$__timeTo()", "'2026-10-12T12:00:00Z'"
            )
            for name in ("source", "locale", "variant", "tier", "set"):
                query = query.replace("${" + name + ":sqlstring}", "'all'")
            async with reader.begin() as session:
                await session.execute(text(query))


async def test_feedback_aggregates_match_cli_without_exposing_comment(sessions, reader):
    from goaletto.application.feedback import answer_with_reason, open_prompt, save_comment
    from goaletto.infrastructure.feedback_queries import feedback_report
    from tests.integration.test_feedback_pg import NOW, SETTINGS, _actor, _internal_owner

    manager_id, _, _, _ = await _internal_owner(sessions)
    actor = await _actor(sessions, manager_id, 9001)
    secret = "PRIVATE_COMMENT_NOT_FOR_GRAFANA"
    async with sessions.begin() as session:
        prompt = await open_prompt(session, actor, slug="operator-check", settings=SETTINGS, at=NOW)
        await answer_with_reason(
            session, actor, response_id=prompt.response_id, reason_code="long_wait", at=NOW
        )
        await save_comment(session, actor, response_id=prompt.response_id, text=secret, at=NOW)
    async with sessions.begin() as session:
        expected = await feedback_report(session, COHORT_FROM, NOW + timedelta(minutes=1))
    response = await sql_report(reader, "feedback", at=NOW + timedelta(minutes=1))
    actual = response["rows"]
    assert len(actual) == 1 and actual[0]["offered_N"] == actual[0]["answered_n"] == 1
    for row in expected["rows"]:
        row.pop("answer_rate")
        row.pop("skip_rate")
    assert actual == expected["rows"]
    reasons = await sql_report(reader, "feedback_reasons", at=NOW + timedelta(minutes=1))
    assert reasons["rows"] == expected["reasons"]
    assert secret not in json.dumps([response, reasons])


async def test_tier_filter_uses_claim_membership_and_recovery_keeps_maturity(sessions, reader):
    from dataclasses import replace

    from sqlalchemy import select

    from goaletto.application.analytics import Reason, Surface
    from goaletto.application.onboarding_measurement import rejection
    from goaletto.infrastructure.models import Club, League, LeagueSet
    from tests.integration.test_agent_wave import NOW, setup_world

    world, actor, club = await setup_world(sessions)
    at = NOW + timedelta(hours=1)
    async with sessions.begin() as session:
        rejected = replace(actor, operation_id=uuid4(), at=NOW + timedelta(minutes=10))
        await rejection(session, rejected, Surface.SQUAD, Reason.LINEUP_INVALID)
        waiting = replace(actor, operation_id=uuid4(), at=at - timedelta(minutes=1))
        await rejection(session, waiting, Surface.SQUAD, Reason.LINEUP_INVALID)
        target = await session.scalar(
            select(League).where(League.league_set_id == world.league_set_id, League.tier == 1)
        )
        (await session.get(Club, club)).league_id = target.id
        set_name = (await session.get(LeagueSet, world.league_set_id)).name
        expected = await launch_report(session, NOW, at, report="recovery")
    actual = await sql_report(reader, "recovery", start=NOW, at=at)
    assert actual["rows"] == expected["rows"]
    row = actual["rows"][0]
    assert row["errors"] == 2 and row["eligible_N"] == 1 and row["waiting"] == 1
    assert row["help_unconfirmed"] == 2 and row["recovered_n"] == 0
    actual = await sql_report(reader, "entry", {"tier": "5", "set": set_name}, start=NOW, at=at)
    assert actual["rows"][0]["starts"] == 1
    assert (await sql_report(reader, "entry", {"tier": "1"}, start=NOW, at=at))["rows"][0][
        "starts"
    ] == 0


async def test_mature_opportunities_use_real_results_and_do_not_count_late_participation(
    sessions, reader
):
    from dataclasses import replace
    from datetime import UTC, datetime

    from sqlalchemy import select

    from goaletto.application.analytics import JourneyEvent, Origin, Surface
    from goaletto.application.game_actions import user_effect
    from goaletto.application.results import finalize_result
    from goaletto.domain.world import WorldOptions
    from goaletto.infrastructure.analytics import record_event
    from goaletto.infrastructure.models import Fixture, OwnershipPeriod
    from tests.integration.test_agent_wave import job, setup_world, start_fixture
    from tests.integration.test_match_practice_runtime import completed_checkpoint

    start = (datetime.now(UTC) - timedelta(days=40)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    options = WorldOptions(
        key="grafana-opportunities",
        seed=0,
        first_kickoff=(start + timedelta(days=4)).replace(hour=18),
    )
    _, actor, club = await setup_world(sessions, options=options, at=start)
    first, first_at = await start_fixture(sessions, club)
    await completed_checkpoint(sessions, first, first_at)
    async with sessions.begin() as session:
        await finalize_result(session, first, first_at + timedelta(minutes=33))
        owner = await session.scalar(select(OwnershipPeriod))
        await user_effect(
            session,
            replace(actor, at=first_at, operation_id=uuid4()),
            "match_opened",
            Surface.MATCH,
            ownership=owner,
            club_id=club,
            fixture_id=first,
        )
        second = await session.scalar(
            select(Fixture)
            .where(
                Fixture.competition == "league",
                Fixture.id != first,
                (Fixture.home_club_id == club) | (Fixture.away_club_id == club),
            )
            .order_by(Fixture.scheduled_start_at)
            .limit(1)
        )
        second_id = second.id
    await job(sessions, "match.lock_lineup", second_id)
    second_at = await job(sessions, "match.start", second_id)
    await completed_checkpoint(sessions, second_id, second_at)
    at = second_at + timedelta(days=1, minutes=33)
    async with sessions.begin() as session:
        await finalize_result(session, second_id, second_at + timedelta(minutes=33))
        await record_event(
            session,
            JourneyEvent(
                event_id=uuid4(),
                name="match_report_opened",
                origin=Origin.USER,
                operation_id=uuid4(),
                effect_number=0,
                surface=Surface.REPORT,
                occurred_at=second_at + timedelta(minutes=35),
                recorded_at=at + timedelta(hours=1),
                analytics_manager_id=actor.analytics_id,
                session_id=uuid4(),
                club_id=club,
                ownership_id=owner.id,
                fixture_id=second_id,
            ),
        )
        expected = await launch_report(session, start, at, report="match-opportunities")
    actual = await sql_report(reader, "opportunities", start=start, at=at)
    assert actual["rows"] == expected["rows"]
    rows = {(r["stage"], r["state"]): r for r in actual["rows"]}
    assert rows[("first", "mature")]["managers"] == rows[("first", "mature")]["participated_n"] == 1
    assert rows[("second", "mature")]["managers"] == rows[("second", "mature")]["unknown_late"] == 1
    assert rows[("second", "mature")]["participated_n"] == 0
