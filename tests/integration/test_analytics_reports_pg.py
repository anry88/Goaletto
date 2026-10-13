"""Runnable PostgreSQL checks for analytics reports on the single Goaletto dev DB.

Run only at the agreed time while development data has no real users::

    GOALETTO_ALLOW_DEVELOPMENT_RESET=1 python -m pytest -q -m integration \\
        tests/integration/test_analytics_reports_pg.py

The fixture truncates the whole application database before each test.
Expected n/N are hand-calculated in the test bodies; SQL must match them.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from goaletto.infrastructure.analytics_reports_queries import delivery, entry_funnel, retention
from goaletto.infrastructure.models import (
    AcquisitionFirstTouch,
    AnalyticsCoverage,
    AnalyticsManager,
    JourneyEventRow,
    JourneySession,
    Manager,
    OutboxDeliveryAttempt,
    OutboxMessage,
)

pytestmark = pytest.mark.integration

T = datetime(2026, 10, 12, 12, tzinfo=UTC)
START = datetime(2026, 10, 11, 12, tzinfo=UTC)
COVERAGE_AT = datetime(2026, 10, 1, tzinfo=UTC)
COHORT_FROM = datetime(2026, 10, 11, tzinfo=UTC)


async def _manager(session, telegram_id: int, internal: bool = False):  # type: ignore[no-untyped-def]
    manager = Manager(
        id=uuid4(),
        telegram_id=telegram_id,
        is_internal=internal,
        created_at=START,
    )
    session.add(manager)
    await session.flush()
    mapping = AnalyticsManager(id=uuid4(), manager_id=manager.id, created_at=START)
    session.add(mapping)
    await session.flush()
    return manager, mapping


async def _start(session, mapping, at: datetime, kind: str = "direct"):  # type: ignore[no-untyped-def]
    session.add(
        AcquisitionFirstTouch(
            analytics_manager_id=mapping.id,
            operation_id=uuid4(),
            kind=kind,
            campaign_id=None,
            started_at=at,
        )
    )
    await session.flush()


async def _session(session, mapping, at: datetime):  # type: ignore[no-untyped-def]
    row = JourneySession(
        id=uuid4(),
        analytics_manager_id=mapping.id,
        first_action_at=at,
        last_action_at=at,
    )
    session.add(row)
    await session.flush()
    return row


async def _event(
    session,
    mapping,
    session_row,
    name,
    origin,
    occurred,
    recorded,  # type: ignore[no-untyped-def]
    ownership=None,
    user_activity=False,
    operation=None,
):  # type: ignore[no-untyped-def]
    operation = operation or uuid4()
    envelope: dict[str, object] = {}
    if origin == "user":
        envelope["session_id"] = str(session_row.id)
    if ownership is not None:
        envelope["ownership_id"] = str(ownership)
    session.add(
        JourneyEventRow(
            id=uuid4(),
            version=1,
            operation_id=operation,
            effect_number=0,
            name=name,
            origin=origin,
            analytics_manager_id=mapping.id,
            session_id=session_row.id if origin == "user" else None,
            occurred_at=occurred,
            recorded_at=recorded,
            user_activity=user_activity,
            envelope=envelope,  # type: ignore[arg-type]
        )
    )
    await session.flush()


async def test_entry_funnel_pg_matches_hand_counted_nn(sessions) -> None:  # type: ignore[no-untyped-def]
    async with sessions.begin() as session:
        session.add(AnalyticsCoverage(version=1, started_at=COVERAGE_AT, retention_days=180))
        # Hand count: 3 mature external starts, 1 waiting; claimed 2/3, ready 1/3.
        managers = []
        for telegram_id in (101, 102, 103, 104):
            at = START if telegram_id != 104 else START + timedelta(hours=1)
            manager, mapping = await _manager(session, telegram_id)
            await _start(session, mapping, at)
            managers.append((manager, mapping))
        own_a, own_b = uuid4(), uuid4()
        row_a = await _session(session, managers[0][1], START + timedelta(hours=1))
        await _event(
            session,
            managers[0][1],
            row_a,
            "club_claimed",
            "user",
            START + timedelta(hours=1),
            START + timedelta(hours=1),
            ownership=own_a,
            user_activity=True,
        )
        await _event(
            session,
            managers[0][1],
            row_a,
            "lineup_saved",
            "user",
            START + timedelta(hours=2),
            START + timedelta(hours=2),
            ownership=own_a,
            user_activity=True,
            operation=uuid4(),
        )
        row_b = await _session(session, managers[1][1], START + timedelta(hours=1))
        await _event(
            session,
            managers[1][1],
            row_b,
            "club_claimed",
            "user",
            START + timedelta(hours=1),
            START + timedelta(hours=1),
            ownership=own_b,
            user_activity=True,
        )
        # Internal manager is excluded even with a claim in the window.
        _, internal_mapping = await _manager(session, 105, internal=True)
        await _start(session, internal_mapping, START)
        internal_session = await _session(session, internal_mapping, START)
        await _event(
            session,
            internal_mapping,
            internal_session,
            "club_claimed",
            "user",
            START + timedelta(hours=1),
            START + timedelta(hours=1),
            ownership=uuid4(),
            user_activity=True,
        )
    async with sessions.begin() as session:
        report = await entry_funnel(session, COHORT_FROM, T)
    assert (report.starts, report.eligible, report.waiting) == (4, 3, 1)
    assert (report.claimed, report.ready) == (2, 1)


async def test_retention_pg_counts_only_user_activity(sessions) -> None:  # type: ignore[no-untyped-def]
    async with sessions.begin() as session:
        session.add(AnalyticsCoverage(version=1, started_at=COVERAGE_AT, retention_days=180))
        manager, mapping = await _manager(session, 201)
        await _start(session, mapping, START)
        row = await _session(session, mapping, START + timedelta(days=1, hours=1))
        # System delivery/simulation in the D1 window must not create retention.
        await _event(
            session,
            mapping,
            row,
            "result_delivered",
            "system",
            START + timedelta(days=1, hours=1),
            START + timedelta(days=1, hours=1),
        )
        active_at = START + timedelta(days=1, hours=2)
        row2 = await _session(session, mapping, active_at)
        await _event(
            session,
            mapping,
            row2,
            "club_opened",
            "user",
            active_at,
            active_at,
            ownership=uuid4(),
            user_activity=True,
        )
    async with sessions.begin() as session:
        report = await retention(session, COHORT_FROM, START + timedelta(days=2))
    d1 = next(row for row in report.rows if row.day == 1)
    assert (d1.eligible_N, d1.retained_n) == (1, 1)


async def test_delivery_pg_keeps_unknown_and_waiting(sessions) -> None:  # type: ignore[no-untyped-def]
    window_from = datetime(2026, 10, 10, tzinfo=UTC)
    async with sessions.begin() as session:
        sent = OutboxMessage(
            id=uuid4(),
            dedupe_key=f"pg-sent-{uuid4()}",
            chat_id=1,
            payload={"kind": "match_result"},
            due_at=window_from,
            status="sent",
            attempts=1,
            telegram_message_id=7,
        )
        unknown = OutboxMessage(
            id=uuid4(),
            dedupe_key=f"pg-unknown-{uuid4()}",
            chat_id=1,
            payload={"kind": "match_board"},
            due_at=window_from,
            status="failed",
            attempts=1,
            last_error="unknown_outcome",
        )
        pending = OutboxMessage(
            id=uuid4(),
            dedupe_key=f"pg-pending-{uuid4()}",
            chat_id=1,
            payload={"kind": "match_board"},
            due_at=window_from,
            status="pending",
            attempts=1,
        )
        session.add_all([sent, unknown, pending])
        await session.flush()
        session.add_all(
            [
                OutboxDeliveryAttempt(
                    outbox_id=sent.id,
                    attempt=1,
                    lease_owner="pg",
                    started_at=window_from,
                    network_started_at=window_from,
                    finished_at=window_from,
                    outcome="sent",
                ),
                OutboxDeliveryAttempt(
                    outbox_id=unknown.id,
                    attempt=1,
                    lease_owner="pg",
                    started_at=window_from,
                    network_started_at=window_from,
                    finished_at=window_from,
                    outcome="unknown",
                ),
                OutboxDeliveryAttempt(
                    outbox_id=pending.id,
                    attempt=1,
                    lease_owner="pg",
                    started_at=window_from,
                    network_started_at=window_from,
                    finished_at=None,
                    outcome="sending",
                ),
            ]
        )
    async with sessions.begin() as session:
        report = await delivery(session, window_from, T)
    assert report.messages_total_N == 3
    assert report.messages_delivered_n == 1
    assert report.messages_failed_unknown == 1
    assert report.messages_pending_waiting == 1
    assert report.attempts_unknown == 1 and report.attempts_waiting == 1
    # Missing/in-flight outcomes are never promoted to delivered.
    assert report.messages_delivered_n == 1
