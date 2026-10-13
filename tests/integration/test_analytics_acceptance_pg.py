"""PG acceptance matrix for analytics reports (AND-82, part of AND-82).

Runnable only at the agreed time on the single player-free Goaletto dev DB::

    GOALETTO_ALLOW_DEVELOPMENT_RESET=1 python -m pytest -q -m integration \\
        tests/integration/test_analytics_acceptance_pg.py

Uses the existing ``sessions`` fixture (which truncates the one dev DB);
no new databases, no real accounts, no personal data or comment text in
fixtures. Expected n/N are hand-calculated literals; the real SQL readers
must match them. These cases extend — and do not repeat — the three simple
checks in ``test_analytics_reports_pg.py``.

Execution in this submission is honestly marked "awaits Codex": unit
matrix passes locally, PG collection is verified, PG execution runs
sequentially after the world is saved.
"""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest

from goaletto.infrastructure.acquisition_queries import source_funnel
from goaletto.infrastructure.analytics_reports_queries import delivery, entry_funnel, retention
from goaletto.infrastructure.models import (
    AcquisitionCampaign,
    AcquisitionFirstTouch,
    AnalyticsCoverage,
    AnalyticsExclusion,
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
    manager = Manager(id=uuid4(), telegram_id=telegram_id, is_internal=internal, created_at=START)
    session.add(manager)
    await session.flush()
    mapping = AnalyticsManager(id=uuid4(), manager_id=manager.id, created_at=START)
    session.add(mapping)
    await session.flush()
    return manager, mapping


async def _campaign(session):  # type: ignore[no-untyped-def]
    campaign = AcquisitionCampaign(
        id=uuid4(),
        code="launch2026",
        source="community",
        placement="launch",
        published_on=date(2026, 10, 12),
        created_at=START,
    )
    session.add(campaign)
    await session.flush()
    return campaign


async def _start(session, mapping, at: datetime, kind: str = "direct", campaign_id=None):  # type: ignore[no-untyped-def]
    session.add(
        AcquisitionFirstTouch(
            analytics_manager_id=mapping.id,
            operation_id=uuid4(),
            kind=kind,
            campaign_id=campaign_id,
            started_at=at,
        )
    )
    await session.flush()


async def _session(session, mapping, at: datetime):  # type: ignore[no-untyped-def]
    row = JourneySession(
        id=uuid4(), analytics_manager_id=mapping.id, first_action_at=at, last_action_at=at
    )
    session.add(row)
    await session.flush()
    return row


async def _event(  # type: ignore[no-untyped-def]
    session,
    mapping,
    session_row,
    name,
    origin,
    occurred,
    recorded,
    ownership=None,
    user_activity=False,
):
    envelope: dict[str, object] = {}
    if origin == "user" and session_row is not None:
        envelope["session_id"] = str(session_row.id)
    if ownership is not None:
        envelope["ownership_id"] = str(ownership)
    session.add(
        JourneyEventRow(
            id=uuid4(),
            version=1,
            operation_id=uuid4(),
            effect_number=0,
            name=name,
            origin=origin,
            analytics_manager_id=mapping.id,
            session_id=session_row.id if origin == "user" and session_row is not None else None,
            occurred_at=occurred,
            recorded_at=recorded,
            user_activity=user_activity,
            envelope=envelope,  # type: ignore[arg-type]
        )
    )
    await session.flush()


async def _message(
    session, kind: str, status: str, due_at: datetime, last_error=None, tag: str = "pg"
):  # type: ignore[no-untyped-def]
    message = OutboxMessage(
        id=uuid4(),
        dedupe_key=f"{tag}-{uuid4()}",
        chat_id=7,
        payload={"kind": kind},
        due_at=due_at,
        status=status,
        attempts=1,
        last_error=last_error,
    )
    session.add(message)
    await session.flush()
    return message


async def _attempt(
    session, message, outcome: str, at: datetime, attempt: int = 1, finished: bool = True
):  # type: ignore[no-untyped-def]
    session.add(
        OutboxDeliveryAttempt(
            outbox_id=message.id,
            attempt=attempt,
            lease_owner="pg-acceptance",
            started_at=at,
            network_started_at=at,
            finished_at=at if finished else None,
            outcome=outcome,
        )
    )
    await session.flush()


async def test_pg_attribution_direct_unknown_known_with_exclusions(sessions) -> None:  # type: ignore[no-untyped-def]
    """Hand: direct 1, unknown 1, known 1 (campaign) are mature; internal and
    opt-out touches are excluded. Source rows group 1/1/1; entry starts 3."""
    async with sessions.begin() as session:
        session.add(AnalyticsCoverage(version=1, started_at=COVERAGE_AT, retention_days=180))
        campaign = await _campaign(session)
        _, direct = await _manager(session, 1001)
        await _start(session, direct, START, kind="direct")
        _, unknown = await _manager(session, 1002)
        await _start(session, unknown, START, kind="unknown")
        _, known = await _manager(session, 1003)
        await _start(session, known, START, kind="known", campaign_id=campaign.id)
        _, internal_mapping = await _manager(session, 1004, internal=True)
        await _start(session, internal_mapping, START, kind="direct")
        opt_manager, opt_mapping = await _manager(session, 1005)
        await _start(session, opt_mapping, START, kind="direct")
        session.add(
            AnalyticsExclusion(manager_id=opt_manager.id, kind="opt_out", excluded_at=START)
        )
    async with sessions.begin() as session:
        funnel = await source_funnel(session, COHORT_FROM, T)
        entry = await entry_funnel(session, COHORT_FROM, T)
    by_kind = {row["kind"]: row for row in funnel.rows}
    # Hand: one mature start per attribution class; internal/opt-out absent.
    assert set(by_kind) == {"direct", "unknown", "known"}
    assert (by_kind["direct"]["starts"], by_kind["direct"]["eligible"]) == (1, 1)
    assert (by_kind["unknown"]["starts"], by_kind["unknown"]["eligible"]) == (1, 1)
    assert (by_kind["known"]["starts"], by_kind["known"]["eligible"]) == (1, 1)
    assert by_kind["known"]["campaign_id"] == campaign.id
    assert (entry.starts, entry.eligible, entry.waiting) == (3, 3, 0)


async def test_pg_same_ownership_ready_and_retry_dedupe(sessions) -> None:  # type: ignore[no-untyped-def]
    """Hand: manager A claim(ownA)+save(ownB) -> claimed only; manager B
    repeats claim+save of ownC -> still one claimed and one ready. Entry:
    starts 2, eligible 2, claimed 2/2, ready 1/2."""
    async with sessions.begin() as session:
        session.add(AnalyticsCoverage(version=1, started_at=COVERAGE_AT, retention_days=180))
        _, mapping_a = await _manager(session, 1101)
        await _start(session, mapping_a, START)
        _, mapping_b = await _manager(session, 1102)
        await _start(session, mapping_b, START)
        row_a = await _session(session, mapping_a, START + timedelta(hours=1))
        await _event(
            session,
            mapping_a,
            row_a,
            "club_claimed",
            "user",
            START + timedelta(hours=1),
            START + timedelta(hours=1),
            ownership=uuid4(),
            user_activity=True,
        )
        await _event(
            session,
            mapping_a,
            row_a,
            "lineup_saved",
            "user",
            START + timedelta(hours=2),
            START + timedelta(hours=2),
            ownership=uuid4(),
            user_activity=True,
        )
        own_c = uuid4()
        row_b = await _session(session, mapping_b, START + timedelta(hours=1))
        for hours in (1, 1, 2, 3):
            name = "club_claimed" if hours == 1 else "lineup_saved"
            occurred = START + timedelta(hours=hours)
            await _event(
                session,
                mapping_b,
                row_b,
                name,
                "user",
                occurred,
                occurred,
                ownership=own_c,
                user_activity=True,
            )
        # System autosquad for B must not create a second readiness signal.
        await _event(
            session,
            mapping_b,
            None,
            "lineup_saved",
            "system",
            START + timedelta(hours=4),
            START + timedelta(hours=4),
            ownership=own_c,
        )
    async with sessions.begin() as session:
        report = await entry_funnel(session, COHORT_FROM, T)
    assert (report.starts, report.eligible, report.waiting) == (2, 2, 0)
    assert (report.claimed, report.ready) == (2, 1)


async def test_pg_late_recorded_is_unknown_then_matures(sessions) -> None:  # type: ignore[no-untyped-def]
    """Hand: claim occurred 13:00, recorded after T. As-of T: claimed 0/1,
    unknown_claimed_late 1. As-of T+2h: claimed 1/1, unknown 0."""
    own = uuid4()
    async with sessions.begin() as session:
        session.add(AnalyticsCoverage(version=1, started_at=COVERAGE_AT, retention_days=180))
        _, mapping = await _manager(session, 1201)
        await _start(session, mapping, START)
        row = await _session(session, mapping, START + timedelta(hours=1))
        await _event(
            session,
            mapping,
            row,
            "club_claimed",
            "user",
            START + timedelta(hours=1),
            T + timedelta(hours=1),
            ownership=own,
            user_activity=True,
        )
    async with sessions.begin() as session:
        as_of = await entry_funnel(session, COHORT_FROM, T)
    assert (as_of.eligible, as_of.claimed, as_of.unknown_claimed_late) == (1, 0, 1)
    async with sessions.begin() as session:
        later = await entry_funnel(session, COHORT_FROM, T + timedelta(hours=2))
    assert (later.eligible, later.claimed, later.unknown_claimed_late) == (1, 1, 0)


async def test_pg_retention_d7_boundaries_and_system_noise(sessions) -> None:  # type: ignore[no-untyped-def]
    """Hand: two mature D7 managers. E hits exactly [18 Oct 12:00, 19 Oct
    12:00); F acts at 11:59 (miss) plus system noise. D7: eligible 2,
    retained 1/2. D1: system-only noise is not retention."""
    d7_low = START + timedelta(days=7)
    d7_high = START + timedelta(days=8)
    async with sessions.begin() as session:
        session.add(AnalyticsCoverage(version=1, started_at=COVERAGE_AT, retention_days=180))
        _, mapping_e = await _manager(session, 1301)
        await _start(session, mapping_e, START)
        _, mapping_f = await _manager(session, 1302)
        await _start(session, mapping_f, START)
        row_e = await _session(session, mapping_e, d7_low)
        await _event(
            session,
            mapping_e,
            row_e,
            "club_opened",
            "user",
            d7_low,
            d7_low,
            ownership=uuid4(),
            user_activity=True,
        )
        row_f = await _session(session, mapping_f, d7_low - timedelta(minutes=1))
        await _event(
            session,
            mapping_f,
            row_f,
            "club_opened",
            "user",
            d7_low - timedelta(minutes=1),
            d7_low - timedelta(minutes=1),
            ownership=uuid4(),
            user_activity=True,
        )
        # System delivery/simulation and a rejected action in the D7 window.
        await _event(session, mapping_f, None, "result_delivered", "system", d7_low, d7_low)
        await _event(session, mapping_f, None, "match_command_applied", "system", d7_low, d7_low)
        await _event(session, mapping_f, row_f, "action_rejected", "user", d7_low, d7_low)
    async with sessions.begin() as session:
        report = await retention(session, COHORT_FROM, d7_high)
    d7 = next(row for row in report.rows if row.day == 7)
    assert (d7.eligible_N, d7.waiting, d7.retained_n) == (2, 0, 1)
    d1 = next(row for row in report.rows if row.day == 1)
    assert (d1.eligible_N, d1.retained_n) == (2, 0)


async def test_pg_delivery_full_outcome_matrix(sessions) -> None:  # type: ignore[no-untyped-def]
    """Hand: 5 messages. sent(with edited+duplicate retry) 1, blocked 1,
    skip_stale 1, unknown_outcome 1, pending sending in-flight 1.
    Delivered 1/5, failed_known 2/5, failed_unknown 1/5, pending 1/5,
    messages_unknown 1, attempts_unknown 1, attempts_waiting 1."""
    window_from = datetime(2026, 10, 10, tzinfo=UTC)
    async with sessions.begin() as session:
        sent = await _message(session, "match_result", "sent", window_from, tag="m-sent")
        blocked = await _message(
            session, "match_result", "failed", window_from, last_error="blocked", tag="m-blocked"
        )
        stale = await _message(
            session, "match_board", "failed", window_from, last_error="skip_stale", tag="m-stale"
        )
        unknown = await _message(
            session,
            "match_board",
            "failed",
            window_from,
            last_error="unknown_outcome",
            tag="m-unknown",
        )
        inflight = await _message(session, "match_board", "pending", window_from, tag="m-flight")
        await _attempt(session, sent, "transient", window_from, attempt=1)
        await _attempt(session, sent, "sent", window_from, attempt=2)
        await _attempt(session, sent, "edited", window_from, attempt=3)
        await _attempt(session, blocked, "blocked", window_from)
        await _attempt(session, stale, "skip_stale", window_from)
        await _attempt(session, unknown, "unknown", window_from)
        await _attempt(session, inflight, "sending", window_from, finished=False)
    async with sessions.begin() as session:
        report = await delivery(session, window_from, T)
    assert report.messages_total_N == 5
    assert report.messages_delivered_n == 1
    assert report.messages_failed_known == 2
    assert report.messages_failed_unknown == 1
    assert report.messages_pending_waiting == 1
    assert report.messages_unknown == 1
    assert report.attempts_total == 8
    assert report.attempts_unknown == 1
    assert report.attempts_waiting == 1
    assert report.messages_delivered_n == 1  # never promoted from missing outcomes
    assert report.as_dict()["historical_outcomes_available"] is False


async def test_pg_empty_zero_mature_and_removed_mapping(sessions) -> None:  # type: ignore[no-untyped-def]
    """Hand: coverage only -> starts 0, eligible 0; delivery window empty ->
    total 0. Then two immature starts -> waiting 2, eligible 0, n 0. Then
    the only mature mapping is deleted -> starts return to 0."""
    async with sessions.begin() as session:
        session.add(AnalyticsCoverage(version=1, started_at=COVERAGE_AT, retention_days=180))
    async with sessions.begin() as session:
        empty_entry = await entry_funnel(session, COHORT_FROM, T)
        empty_retention = await retention(session, COHORT_FROM, T)
        empty_delivery = await delivery(session, datetime(2026, 10, 10, tzinfo=UTC), T)
    assert (empty_entry.starts, empty_entry.eligible, empty_entry.waiting) == (0, 0, 0)
    assert (empty_entry.claimed, empty_entry.ready) == (0, 0)
    assert empty_retention.starts == 0
    assert empty_delivery.messages_total_N == 0
    assert empty_delivery.attempts_total == 0
    async with sessions.begin() as session:
        _, mapping_a = await _manager(session, 1401)
        await _start(session, mapping_a, T - timedelta(hours=1))
        _, mapping_b = await _manager(session, 1402)
        await _start(session, mapping_b, T - timedelta(hours=2))
    async with sessions.begin() as session:
        immature = await entry_funnel(session, COHORT_FROM, T)
    # Hand: both starts are younger than 24h -> waiting 2, eligible 0.
    assert (immature.starts, immature.eligible, immature.waiting) == (2, 0, 2)
    assert (immature.claimed, immature.ready) == (0, 0)
    async with sessions.begin() as session:
        for mapping in (mapping_a, mapping_b):
            await session.delete(mapping)
    async with sessions.begin() as session:
        removed = await entry_funnel(session, COHORT_FROM, T)
    assert (removed.starts, removed.eligible, removed.waiting) == (0, 0, 0)
