"""Synthetic n/N checks for operator analytics reports; no database is created."""

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics_reports import (
    SyntheticAttempt,
    SyntheticEvent,
    SyntheticMessage,
    SyntheticStart,
    evaluate_delivery,
    evaluate_entry_funnel,
    evaluate_retention,
    resolve_coverage,
    validate_delivery_report,
    validate_entry_funnel,
    validate_retention_report,
)
from goaletto.infrastructure import analytics_reports_queries as queries
from goaletto.infrastructure.models import AnalyticsCoverage

T = datetime(2026, 10, 12, 12, tzinfo=UTC)
START = datetime(2026, 10, 11, 12, tzinfo=UTC)


def coverage(**overrides):  # type: ignore[no-untyped-def]
    params = {
        "coverage_started_at": datetime(2026, 10, 1, tzinfo=UTC),
        "retention_days": 180,
        "requested_from": datetime(2026, 10, 11, tzinfo=UTC),
        "report_at": T,
    }
    params.update(overrides)
    return resolve_coverage(**params)  # type: ignore[arg-type]


def start(at: datetime, **flags):  # type: ignore[no-untyped-def]
    return SyntheticStart(uuid4(), at, **flags)  # type: ignore[arg-type]


def claim(manager_id, at: datetime, ownership, recorded=None) -> SyntheticEvent:
    return SyntheticEvent(
        manager_id,
        "club_claimed",
        "user",
        at,
        recorded or at,
        ownership_id=ownership,
        user_activity=True,
    )


def saved(manager_id, at: datetime, ownership, recorded=None, origin="user") -> SyntheticEvent:
    return SyntheticEvent(
        manager_id,
        "lineup_saved",
        origin,
        at,
        recorded or at,
        ownership_id=ownership,
        user_activity=(origin == "user"),
    )


def user_action(manager_id, at: datetime, recorded=None, name="club_opened") -> SyntheticEvent:
    return SyntheticEvent(
        manager_id,
        name,
        "user",
        at,
        recorded or at,
        user_activity=True,
    )


def system_event(manager_id, at: datetime, name: str) -> SyntheticEvent:
    return SyntheticEvent(manager_id, name, "system", at, at, user_activity=False)


def test_entry_funnel_maturity_claim_ready_and_retry_dedupe() -> None:
    own_a, own_b = uuid4(), uuid4()
    managers = (start(START), start(START), start(START), start(START + timedelta(hours=1)))
    events: list[SyntheticEvent] = [
        claim(managers[0].analytics_manager_id, START + timedelta(hours=1), own_a),
        saved(managers[0].analytics_manager_id, START + timedelta(hours=2), own_a),
        # Repeat manual save by the same manager must not double count.
        saved(managers[0].analytics_manager_id, START + timedelta(hours=3), own_a),
        claim(managers[1].analytics_manager_id, START + timedelta(hours=1), own_b),
        # Autosquad is system origin: claim counts, manual readiness does not.
        saved(managers[1].analytics_manager_id, START + timedelta(hours=2), own_b, origin="system"),
    ]
    report = evaluate_entry_funnel(tuple(managers), tuple(events), coverage())
    assert (report.starts, report.eligible, report.waiting) == (4, 3, 1)
    assert (report.claimed, report.ready) == (2, 1)
    validate_entry_funnel(report.as_dict())
    # Manual n/N: claimed 2/3, ready 1/3; the immature fourth start stays waiting.


def test_entry_funnel_excludes_internal_and_opt_out() -> None:
    managers = (
        start(START),
        start(START, is_internal=True),
        start(START, excluded=True),
    )
    own = uuid4()
    events = (
        claim(managers[0].analytics_manager_id, START + timedelta(hours=1), own),
        claim(managers[1].analytics_manager_id, START + timedelta(hours=1), uuid4()),
        claim(managers[2].analytics_manager_id, START + timedelta(hours=1), uuid4()),
    )
    report = evaluate_entry_funnel(managers, events, coverage())
    assert (report.starts, report.eligible, report.claimed) == (1, 1, 1)
    validate_entry_funnel(report.as_dict())


def test_entry_funnel_late_recorded_is_unknown_then_matures() -> None:
    manager = start(START)
    own = uuid4()
    late_claim = claim(
        manager.analytics_manager_id,
        START + timedelta(hours=1),
        own,
        recorded=T + timedelta(hours=1),
    )
    late_saved = saved(
        manager.analytics_manager_id,
        START + timedelta(hours=2),
        own,
        recorded=T + timedelta(hours=1),
    )
    as_of = evaluate_entry_funnel((manager,), (late_claim, late_saved), coverage())
    assert (as_of.claimed, as_of.ready) == (0, 0)
    assert (as_of.unknown_claimed_late, as_of.unknown_ready_late) == (1, 1)
    later = evaluate_entry_funnel(
        (manager,),
        (late_claim, late_saved),
        coverage(report_at=T + timedelta(hours=2)),
    )
    assert (later.claimed, later.ready) == (1, 1)
    assert (later.unknown_claimed_late, later.unknown_ready_late) == (0, 0)


def test_entry_funnel_ready_needs_same_ownership() -> None:
    manager = start(START)
    events = (
        claim(manager.analytics_manager_id, START + timedelta(hours=1), uuid4()),
        saved(manager.analytics_manager_id, START + timedelta(hours=2), uuid4()),
    )
    report = evaluate_entry_funnel((manager,), events, coverage())
    assert (report.claimed, report.ready) == (1, 0)


def test_retention_day_boundaries_and_maturity() -> None:
    manager = start(START)
    d7_start = START + timedelta(days=7)
    d7_end = START + timedelta(days=8)
    # 11:59 on day 7 is still day 6; exactly at the upper bound is already outside.
    before = user_action(manager.analytics_manager_id, d7_start - timedelta(minutes=1))
    upper = user_action(manager.analytics_manager_id, d7_end)
    report = evaluate_retention(
        (manager,), (before, upper), coverage(report_at=d7_end - timedelta(minutes=1))
    )
    d7 = next(row for row in report.rows if row.day == 7)
    assert (d7.eligible_N, d7.waiting, d7.retained_n) == (0, 1, 0)
    mature = evaluate_retention((manager,), (before, upper), coverage(report_at=d7_end))
    d7_mature = next(row for row in mature.rows if row.day == 7)
    assert (d7_mature.eligible_N, d7_mature.retained_n) == (1, 0)
    exact = user_action(manager.analytics_manager_id, d7_start)
    hit = evaluate_retention((manager,), (exact,), coverage(report_at=d7_end))
    assert next(row for row in hit.rows if row.day == 7).retained_n == 1
    validate_retention_report(hit.as_dict())


def test_retention_counts_only_explicit_manager_actions() -> None:
    manager = start(START)
    window = START + timedelta(days=1, hours=2)
    report_at = START + timedelta(days=2)
    events = (
        system_event(manager.analytics_manager_id, window, "result_delivered"),
        system_event(manager.analytics_manager_id, window, "fixture_finished"),
        system_event(manager.analytics_manager_id, window, "match_command_applied"),
        SyntheticEvent(
            manager.analytics_manager_id,
            "action_rejected",
            "user",
            window,
            window,
            user_activity=False,
        ),
    )
    report = evaluate_retention((manager,), events, coverage(report_at=report_at))
    d1 = next(row for row in report.rows if row.day == 1)
    assert (d1.eligible_N, d1.retained_n, d1.unknown_late) == (1, 0, 0)
    feedback = user_action(
        manager.analytics_manager_id,
        window,
        name="feedback_answered",
    )
    with_feedback = evaluate_retention(
        (manager,), (*events, feedback), coverage(report_at=report_at)
    )
    assert next(row for row in with_feedback.rows if row.day == 1).retained_n == 1


def test_retention_late_recording_is_unknown_not_retained() -> None:
    manager = start(START)
    window = START + timedelta(days=1, hours=1)
    late = user_action(manager.analytics_manager_id, window, recorded=T + timedelta(days=5))
    report = evaluate_retention((manager,), (late,), coverage(report_at=START + timedelta(days=2)))
    d1 = next(row for row in report.rows if row.day == 1)
    assert (d1.retained_n, d1.unknown_late) == (0, 1)


def test_coverage_retention_clipping_marks_limited_window() -> None:
    old = start(datetime(2026, 7, 1, tzinfo=UTC))
    fresh = start(START)
    cov = coverage(
        coverage_started_at=datetime(2026, 10, 1, tzinfo=UTC),
        retention_days=90,
        requested_from=datetime(2026, 6, 1, tzinfo=UTC),
        report_at=T,
    )
    assert cov.coverage_limited
    assert cov.available_from == datetime(2026, 10, 1, tzinfo=UTC)
    report = evaluate_entry_funnel((old, fresh), (), cov)
    assert (report.starts, report.eligible) == (1, 1)


def test_delivery_known_unknown_waiting_and_retry() -> None:
    window_from = datetime(2026, 10, 10, tzinfo=UTC)
    sent_id, blocked_id, unknown_id, sending_id, queued_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    messages = (
        SyntheticMessage(sent_id, "match_result", window_from, "sent"),
        SyntheticMessage(blocked_id, "match_result", window_from, "failed", "blocked"),
        SyntheticMessage(unknown_id, "match_board", window_from, "failed", "unknown_outcome"),
        SyntheticMessage(sending_id, "match_board", window_from, "pending", "unknown_outcome"),
        SyntheticMessage(queued_id, "fixture_reminder", window_from, "pending"),
    )
    attempts = (
        # Retry: transient then sent still counts as one delivered message.
        SyntheticAttempt(sent_id, "transient", window_from, window_from, window_from),
        SyntheticAttempt(
            sent_id,
            "sent",
            window_from + timedelta(minutes=1),
            window_from + timedelta(minutes=1),
            window_from + timedelta(minutes=1),
        ),
        SyntheticAttempt(blocked_id, "blocked", window_from, window_from, window_from),
        SyntheticAttempt(unknown_id, "unknown", window_from, window_from, window_from),
        # network_started_at alone is an in-flight start, not Telegram proof.
        SyntheticAttempt(sending_id, "sending", window_from, window_from, None),
    )
    report = evaluate_delivery(messages, attempts, window_from=window_from, report_at=T)
    assert report.messages_total_N == 5
    assert report.messages_delivered_n == 1
    assert report.messages_failed_known == 1
    assert report.messages_failed_unknown == 1
    assert report.messages_pending_waiting == 2
    # Unknown: exhausted unknown outcome plus pending with an unknown attempt.
    assert report.messages_unknown == 2
    assert report.attempts_total == 5
    assert report.attempts_unknown == 1
    assert report.attempts_waiting == 1
    validate_delivery_report(report.as_dict())
    # Missing outcomes stay pending/unknown: never promoted to sent or failed.
    assert report.messages_delivered_n + report.messages_failed_known == 2


def test_report_validators_reject_mature_denominator_substitution() -> None:
    good = evaluate_entry_funnel((start(START),), (), coverage()).as_dict()
    validate_entry_funnel(good)
    with pytest.raises(ValueError):
        validate_entry_funnel({**good, "ready_n": 2})
    with pytest.raises(ValueError):
        validate_entry_funnel({**good, "waiting": 2})
    retention_good = evaluate_retention((start(START),), (), coverage()).as_dict()
    with pytest.raises(ValueError):
        validate_retention_report({**retention_good, "starts": 99})
    delivery_good = evaluate_delivery(
        (SyntheticMessage(uuid4(), "match_result", START, "sent"),),
        (SyntheticAttempt(uuid4(), "sent", START, START, START),),
        window_from=datetime(2026, 10, 10, tzinfo=UTC),
        report_at=T,
    ).as_dict()
    with pytest.raises(ValueError):
        validate_delivery_report({**delivery_good, "messages_delivered_n": 9})


async def test_entry_sql_excludes_internal_opt_out_and_uses_as_of_recorded() -> None:
    class Session:
        async def scalar(self, statement):  # type: ignore[no-untyped-def]
            return AnalyticsCoverage(version=1, started_at=START, retention_days=180)

        async def execute(self, statement, params):  # type: ignore[no-untyped-def]
            self.sql = str(statement)
            self.params = params

            class Result:
                def mappings(self):  # type: ignore[no-untyped-def]
                    return self

                def one(self):  # type: ignore[no-untyped-def]
                    return {
                        "starts": 4,
                        "eligible": 3,
                        "waiting": 1,
                        "claimed": 2,
                        "ready": 1,
                        "unknown_claimed_late": 1,
                        "unknown_ready_late": 0,
                    }

            return Result()

    session = Session()
    report = await queries.entry_funnel(
        cast(AsyncSession, session), datetime(2026, 10, 11, tzinfo=UTC), T
    )
    assert (report.starts, report.eligible, report.claimed, report.ready) == (4, 3, 2, 1)
    assert session.params == {"cohort_from": report.coverage.available_from, "report_at": T}
    for needle in (
        "NOT m.is_internal",
        "analytics_exclusions",
        "origin = 'user'",
        "lineup_saved",
        "ownership_id",
        "recorded_at <= :report_at",
    ):
        assert needle in session.sql


async def test_retention_sql_uses_user_activity_and_day_windows() -> None:
    seen: list[str] = []

    class Session:
        def __init__(self) -> None:
            self.calls = 0

        async def scalar(self, statement):  # type: ignore[no-untyped-def]
            return AnalyticsCoverage(version=1, started_at=START, retention_days=180)

        async def execute(self, statement, params):  # type: ignore[no-untyped-def]
            seen.append(str(statement))
            self.calls += 1

            class Result:
                def __init__(self, calls: int):
                    self.calls = calls

                def mappings(self):  # type: ignore[no-untyped-def]
                    return self

                def one(self):  # type: ignore[no-untyped-def]
                    retained = 1 if self.calls == 1 else 0
                    return {
                        "starts": 2,
                        "eligible": 1,
                        "waiting": 1,
                        "retained": retained,
                        "unknown_late": 0,
                    }

            return Result(self.calls)

    report = await queries.retention(
        cast(AsyncSession, Session()), datetime(2026, 10, 11, tzinfo=UTC), T
    )
    assert [row.day for row in report.rows] == [1, 3, 7, 30]
    assert "user_activity" in seen[0] and "origin = 'user'" in seen[0]
    assert "INTERVAL '24 hours'" in seen[0] and "INTERVAL '48 hours'" in seen[0]
    assert "INTERVAL '168 hours'" in seen[2]


async def test_delivery_sql_groups_by_kind_and_keeps_unknown() -> None:
    class Session:
        async def execute(self, statement, params):  # type: ignore[no-untyped-def]
            self.sql = str(statement)

            class Result:
                def __init__(self, sql: str):
                    self.sql = sql

                def mappings(self):  # type: ignore[no-untyped-def]
                    return self

                def all(self):  # type: ignore[no-untyped-def]
                    if "GROUP BY outcome" in self.sql:
                        return [
                            {"outcome": "sent", "count": 1},
                            {"outcome": "sending", "count": 1},
                            {"outcome": "unknown", "count": 1},
                        ]
                    return [
                        {
                            "kind": "match_result",
                            "total": 2,
                            "delivered": 1,
                            "failed_known": 1,
                            "failed_unknown": 0,
                            "pending": 0,
                            "unknown_messages": 0,
                        },
                        {
                            "kind": "match_board",
                            "total": 2,
                            "delivered": 0,
                            "failed_known": 0,
                            "failed_unknown": 1,
                            "pending": 1,
                            "unknown_messages": 2,
                        },
                    ]

            return Result(self.sql)

    session = Session()
    report = await queries.delivery(
        cast(AsyncSession, session),
        datetime(2026, 10, 10, tzinfo=UTC),
        T,
    )
    assert report.messages_total_N == 4
    assert report.attempts_unknown == 1 and report.attempts_waiting == 1
    assert report.messages_unknown == 2
