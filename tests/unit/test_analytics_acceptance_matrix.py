"""Acceptance matrix for analytics reports (AND-82, part of AND-82).

Reproducible named cases with hand-calculated n/N that check the current
report API and SQL text and expose semantic errors. This is a verification
of existing analytics, not new instrumentation and not a dashboard.

Scope: ``application/analytics_reports.py`` synthetic evaluators (a mirror
of the SQL), ``infrastructure/analytics_reports_queries.py`` SQL text,
``infrastructure/acquisition_queries.py`` source-funnel SQL text and the
``application/analytics.py`` contract helpers. Expectations below are hand
literals; they are never computed by the function under test.

Real PostgreSQL execution lives in
``tests/integration/test_analytics_acceptance_pg.py`` (run by Codex at the
agreed time). Unavailable feedback / match-opportunity / error-recovery
aggregates are explicitly marked NOT IMPLEMENTED; no fake emitters are
created to force a green test.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from goaletto.application.analytics import (
    EVENT_SPECS,
    Origin,
    match_activity_window,
    retention_window,
    window_is_mature,
)
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
from goaletto.infrastructure import acquisition_queries
from goaletto.infrastructure import analytics_reports_queries as queries

T = datetime(2026, 10, 12, 12, tzinfo=UTC)
START = datetime(2026, 10, 11, 12, tzinfo=UTC)
COVERAGE_AT = datetime(2026, 10, 1, tzinfo=UTC)
COHORT_FROM = datetime(2026, 10, 11, tzinfo=UTC)


def _coverage(
    requested_from: datetime = COHORT_FROM,
    report_at: datetime = T,
    coverage_started_at: datetime = COVERAGE_AT,
    retention_days: int = 180,
):  # type: ignore[no-untyped-def]
    return resolve_coverage(
        coverage_started_at=coverage_started_at,
        retention_days=retention_days,
        requested_from=requested_from,
        report_at=report_at,
    )


def _start(at: datetime, internal: bool = False, excluded: bool = False) -> SyntheticStart:
    return SyntheticStart(uuid4(), at, is_internal=internal, excluded=excluded)


def _claim(
    manager_id: UUID,
    at: datetime,
    ownership: UUID,
    recorded: datetime | None = None,
) -> SyntheticEvent:
    moment = recorded if recorded is not None else at
    return SyntheticEvent(manager_id, "club_claimed", "user", at, moment, ownership, True, 1)


def _saved(
    manager_id: UUID,
    at: datetime,
    ownership: UUID,
    recorded: datetime | None = None,
    origin: str = "user",
) -> SyntheticEvent:
    moment = recorded if recorded is not None else at
    return SyntheticEvent(
        manager_id, "lineup_saved", origin, at, moment, ownership, origin == "user", 1
    )


def _user(
    manager_id: UUID,
    at: datetime,
    name: str = "club_opened",
    recorded: datetime | None = None,
) -> SyntheticEvent:
    moment = recorded if recorded is not None else at
    return SyntheticEvent(manager_id, name, "user", at, moment, None, True, 1)


def _system(manager_id: UUID, at: datetime, name: str) -> SyntheticEvent:
    return SyntheticEvent(manager_id, name, "system", at, at, None, False, 1)


def _rejected(manager_id: UUID, at: datetime) -> SyntheticEvent:
    return SyntheticEvent(manager_id, "action_rejected", "user", at, at, None, False, 1)


# A · attribution and exclusions -------------------------------------------


def test_a1_source_funnel_sql_groups_direct_unknown_known() -> None:
    sql = acquisition_queries.SOURCE_FUNNEL_SQL
    for needle in (
        "a.kind",
        "GROUP BY e.kind, e.campaign_id",
        "LEFT JOIN acquisition_campaigns",
        "NOT m.is_internal",
        "analytics_exclusions",
        "mature AND claimed",
        "mature AND ready",
    ):
        assert needle in sql
    # Hand-shaped aggregate rows: direct 2, unknown 1, known 1 (one campaign).
    # Contract: absent/empty payload -> direct, registered code -> known,
    # unknown/invalid code -> unknown; raw unknown payload is never stored.
    good_direct = {
        "kind": "direct",
        "campaign_id": None,
        "source": None,
        "placement": None,
        "published_on": None,
        "starts": 2,
        "eligible": 2,
        "waiting": 0,
        "claimed": 1,
        "ready": 1,
    }
    acquisition_queries.validate_funnel_row(good_direct)
    campaign_id = uuid4()
    good_known = {
        "kind": "known",
        "campaign_id": campaign_id,
        "source": "community",
        "placement": "launch",
        "published_on": None,
        "starts": 1,
        "eligible": 1,
        "waiting": 0,
        "claimed": 1,
        "ready": 0,
    }
    acquisition_queries.validate_funnel_row(good_known)
    # Known without campaign (or direct with campaign) breaks attribution class.
    bad = dict(good_known, campaign_id=None)
    try:
        acquisition_queries.validate_funnel_row(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("known without campaign_id must be rejected")


def test_a2_internal_and_opt_out_excluded_from_journey() -> None:
    # Hand: 3 first touches, only 1 external non-excluded -> starts 1/eligible 1.
    managers = (_start(START), _start(START, internal=True), _start(START, excluded=True))
    events = (
        _claim(managers[0].analytics_manager_id, START + timedelta(hours=1), uuid4()),
        _claim(managers[1].analytics_manager_id, START + timedelta(hours=1), uuid4()),
        _claim(managers[2].analytics_manager_id, START + timedelta(hours=1), uuid4()),
    )
    report = evaluate_entry_funnel(managers, events, _coverage())
    assert (report.starts, report.eligible, report.waiting) == (1, 1, 0)
    assert (report.claimed, report.ready) == (1, 0)
    validate_entry_funnel(report.as_dict())


def test_a3_deleted_mapping_leaves_empty_cohort() -> None:
    # Hand: mapping removed (opt-out CASCADE) -> no starts at all, not 0/N.
    report = evaluate_entry_funnel((), (), _coverage())
    assert (report.starts, report.eligible, report.waiting) == (0, 0, 0)
    assert (report.claimed, report.ready) == (0, 0)
    validate_entry_funnel(report.as_dict())
    retention = evaluate_retention((), (), _coverage())
    assert retention.starts == 0
    for row in retention.rows:
        assert (row.eligible_N, row.waiting, row.retained_n, row.unknown_late) == (0, 0, 0, 0)
    validate_retention_report(retention.as_dict())


# B · maturity, exact UTC boundaries, coverage --------------------------------


def test_b1_entry_maturity_boundary_is_exact_24h() -> None:
    # Hand: start exactly T-24h is mature; start 1s later is waiting.
    mature_at = T - timedelta(hours=24)
    fresh_at = mature_at + timedelta(seconds=1)
    managers = (_start(mature_at), _start(fresh_at))
    report = evaluate_entry_funnel(managers, (), _coverage())
    assert (report.starts, report.eligible, report.waiting) == (2, 1, 1)
    assert window_is_mature(mature_at + timedelta(hours=24), T)
    assert not window_is_mature(fresh_at + timedelta(hours=24), T)


def test_b2_retention_maturity_per_day_window() -> None:
    # Hand: START=11 Oct 12:00. T=13 Oct 12:00 -> D1 mature only; rest waiting.
    report_at = START + timedelta(days=2)
    report = evaluate_retention((_start(START),), (), _coverage(report_at=report_at))
    by_day = {row.day: row for row in report.rows}
    assert (by_day[1].eligible_N, by_day[1].waiting) == (1, 0)
    assert (by_day[3].eligible_N, by_day[3].waiting) == (0, 1)
    assert (by_day[7].eligible_N, by_day[7].waiting) == (0, 1)
    assert (by_day[30].eligible_N, by_day[30].waiting) == (0, 1)
    validate_retention_report(report.as_dict())
    # T=19 Oct 11:59 (D7 end minus 1min) -> D7 still waiting 1, eligible 0.
    immature_at = START + timedelta(days=8) - timedelta(minutes=1)
    immature = evaluate_retention((_start(START),), (), _coverage(report_at=immature_at))
    d7 = next(row for row in immature.rows if row.day == 7)
    assert (d7.eligible_N, d7.waiting) == (0, 1)


def test_b3_retention_window_is_half_open_utc() -> None:
    # Hand: D7 of START is [18 Oct 12:00, 19 Oct 12:00). 11:59 misses, 12:00
    # hits, upper bound is already outside. Contract example, hand n/N.
    manager = _start(START)
    low, high = retention_window(START, 7)
    assert low == datetime(2026, 10, 18, 12, tzinfo=UTC)
    assert high == datetime(2026, 10, 19, 12, tzinfo=UTC)
    before = _user(manager.analytics_manager_id, low - timedelta(minutes=1))
    upper = _user(manager.analytics_manager_id, high)
    report = evaluate_retention((manager,), (before, upper), _coverage(report_at=high))
    d7 = next(row for row in report.rows if row.day == 7)
    # Hand: neither boundary-violating action counts -> 0/1.
    assert (d7.eligible_N, d7.retained_n, d7.unknown_late) == (1, 0, 0)
    exact = _user(manager.analytics_manager_id, low)
    hit = evaluate_retention((manager,), (exact,), _coverage(report_at=high))
    assert next(row for row in hit.rows if row.day == 7).retained_n == 1


def test_b4_coverage_start_clips_old_starts() -> None:
    # Hand: requested 11 Oct, coverage starts 12 Oct 00:00 -> 11 Oct start
    # excluded, coverage_limited True, starts 0.
    cov = _coverage(coverage_started_at=datetime(2026, 10, 12, tzinfo=UTC))
    assert cov.coverage_limited
    assert cov.available_from == datetime(2026, 10, 12, tzinfo=UTC)
    report = evaluate_entry_funnel((_start(START),), (), cov)
    assert (report.starts, report.eligible, report.waiting) == (0, 0, 0)


def test_b5_retention_cutoff_clips_beyond_retention_days() -> None:
    # Hand: 90-day retention at T=12 Oct 12:00 keeps 01 Oct, drops 01 Jul.
    cov = _coverage(
        requested_from=datetime(2026, 6, 1, tzinfo=UTC),
        retention_days=90,
        coverage_started_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert cov.available_from == T - timedelta(days=90)
    old = _start(datetime(2026, 7, 1, tzinfo=UTC))
    fresh = _start(START)
    report = evaluate_entry_funnel((old, fresh), (), cov)
    # Hand: only the fresh start survives clipping -> starts 1, eligible 1.
    assert (report.starts, report.eligible) == (1, 1)


def test_b6_first_match_after_seven_days_is_waiting_not_missed() -> None:
    # Contract: weekly league; absence between matches is not churn. A first
    # opportunity 7+ days after start with T before finish+24h is immature.
    # Match-opportunity SQL is NOT IMPLEMENTED (see section G); here only the
    # window helper is pinned so a future reader cannot mislabel waiting.
    actual_start = START + timedelta(days=9)
    actual_finish = actual_start + timedelta(minutes=33)
    window_start, window_end = match_activity_window(actual_start, actual_finish)
    assert window_start == actual_start - timedelta(hours=24)
    assert window_end == actual_finish + timedelta(hours=24)
    # T one hour after the match is still inside the +24h window -> immature.
    assert not window_is_mature(window_end, actual_finish + timedelta(hours=1))
    assert window_is_mature(window_end, window_end)
    assert not hasattr(queries, "match_opportunity")
    assert not hasattr(queries, "second_match_return")


def test_b7_identical_timestamps_count_distinct_managers_once() -> None:
    # Hand: two managers start at the same instant; both claim+save at the
    # same instants. Distinct managers -> claimed 2/2, ready 2/2.
    first, second = _start(START), _start(START)
    own_first, own_second = uuid4(), uuid4()
    at_claim = START + timedelta(hours=1)
    at_save = START + timedelta(hours=1)  # same instant: save >= claim holds.
    events = (
        _claim(first.analytics_manager_id, at_claim, own_first),
        _saved(first.analytics_manager_id, at_save, own_first),
        _claim(second.analytics_manager_id, at_claim, own_second),
        _saved(second.analytics_manager_id, at_save, own_second),
    )
    report = evaluate_entry_funnel((first, second), events, _coverage())
    assert (report.starts, report.eligible) == (2, 2)
    assert (report.claimed, report.ready) == (2, 2)


# C · what counts as user activity --------------------------------------------


def test_c1_system_auto_applied_passive_and_rejected_are_not_retention() -> None:
    # Hand: D1 window gets only system/automation/passive/rejected effects ->
    # retained 0/1, unknown 0. One explicit club_opened flips it to 1/1.
    manager = _start(START)
    window = START + timedelta(days=1, hours=2)
    report_at = START + timedelta(days=2)
    noise = (
        _system(manager.analytics_manager_id, window, "fixture_finished"),
        _system(manager.analytics_manager_id, window, "result_delivered"),
        _system(manager.analytics_manager_id, window, "match_command_applied"),
        _system(manager.analytics_manager_id, window, "match_tick_committed"),
        _saved(manager.analytics_manager_id, window, uuid4(), origin="system"),
        _rejected(manager.analytics_manager_id, window),
    )
    report = evaluate_retention((manager,), noise, _coverage(report_at=report_at))
    d1 = next(row for row in report.rows if row.day == 1)
    assert (d1.eligible_N, d1.retained_n, d1.unknown_late) == (1, 0, 0)
    with_hit = evaluate_retention(
        (manager,),
        (*noise, _user(manager.analytics_manager_id, window)),
        _coverage(report_at=report_at),
    )
    assert next(row for row in with_hit.rows if row.day == 1).retained_n == 1


def test_c2_explicit_match_report_open_counts_only_per_event_spec() -> None:
    # Contract pins which names are user activity; applied commands are not.
    assert EVENT_SPECS["match_opened"].user_activity
    assert EVENT_SPECS["match_report_opened"].user_activity
    assert EVENT_SPECS["match_command_accepted"].user_activity
    assert not EVENT_SPECS["match_command_applied"].user_activity
    assert EVENT_SPECS["match_command_applied"].origin == Origin.SYSTEM
    assert not EVENT_SPECS["action_rejected"].user_activity
    assert EVENT_SPECS["feedback_answered"].user_activity
    assert not EVENT_SPECS["feedback_answered"].ownership_activity
    # Hand: accepted (user) in D1 counts, applied (system) does not.
    manager = _start(START)
    window = START + timedelta(days=1, hours=2)
    report_at = START + timedelta(days=2)
    accepted = _user(manager.analytics_manager_id, window, name="match_command_accepted")
    report = evaluate_retention((manager,), (accepted,), _coverage(report_at=report_at))
    assert next(row for row in report.rows if row.day == 1).retained_n == 1


def test_c3_readiness_needs_same_ownership_period() -> None:
    # Hand: claim(ownA)+save(ownB) -> claimed 1/1, ready 0/1. A later save of
    # a different ownership must not complete the previous period.
    manager = _start(START)
    events = (
        _claim(manager.analytics_manager_id, START + timedelta(hours=1), uuid4()),
        _saved(manager.analytics_manager_id, START + timedelta(hours=2), uuid4()),
    )
    report = evaluate_entry_funnel((manager,), events, _coverage())
    assert (report.claimed, report.ready) == (1, 0)
    sql = queries.ENTRY_FUNNEL_SQL
    assert "saved.envelope ->> 'ownership_id' = claim.envelope ->> 'ownership_id'" in sql
    assert "saved.occurred_at >= claim.occurred_at" in sql


def test_c4_replay_rollback_retry_do_not_double_count() -> None:
    # Hand: one manager repeats claim+manual save three times in the window;
    # distinct-manager counting keeps claimed 1/1 and ready 1/1.
    manager = _start(START)
    own = uuid4()
    events = (
        _claim(manager.analytics_manager_id, START + timedelta(hours=1), own),
        _claim(manager.analytics_manager_id, START + timedelta(hours=1), own),
        _saved(manager.analytics_manager_id, START + timedelta(hours=2), own),
        _saved(manager.analytics_manager_id, START + timedelta(hours=3), own),
    )
    report = evaluate_entry_funnel((manager,), events, _coverage())
    assert (report.claimed, report.ready) == (1, 1)
    assert "COUNT(*) FILTER (WHERE mature AND ready)" in queries.ENTRY_FUNNEL_SQL


# D · occurred/recorded, unknown_late, journey vs delivery --------------------


def test_d1_late_recorded_claim_is_unknown_then_matures() -> None:
    # Hand: occurred 13:00 in window, recorded after T -> as-of 0/1 claimed,
    # unknown 1; later T sees 1/1 with unknown 0.
    manager = _start(START)
    own = uuid4()
    claim = _claim(
        manager.analytics_manager_id,
        START + timedelta(hours=1),
        own,
        recorded=T + timedelta(hours=1),
    )
    saved = _saved(
        manager.analytics_manager_id,
        START + timedelta(hours=2),
        own,
        recorded=T + timedelta(hours=1),
    )
    as_of = evaluate_entry_funnel((manager,), (claim, saved), _coverage())
    assert (as_of.claimed, as_of.ready) == (0, 0)
    assert (as_of.unknown_claimed_late, as_of.unknown_ready_late) == (1, 1)
    later = evaluate_entry_funnel(
        (manager,), (claim, saved), _coverage(report_at=T + timedelta(hours=2))
    )
    assert (later.claimed, later.ready) == (1, 1)
    assert (later.unknown_claimed_late, later.unknown_ready_late) == (0, 0)


def test_d2_late_recorded_retention_is_unknown_not_retained() -> None:
    # Hand: D1 action occurred in window but recorded 5 days after T ->
    # retained 0, unknown_late 1.
    manager = _start(START)
    window = START + timedelta(days=1, hours=1)
    late = _user(manager.analytics_manager_id, window, recorded=T + timedelta(days=5))
    report = evaluate_retention((manager,), (late,), _coverage(report_at=START + timedelta(days=2)))
    d1 = next(row for row in report.rows if row.day == 1)
    assert (d1.retained_n, d1.unknown_late) == (0, 1)


def test_d3_journey_is_as_of_delivery_is_current_snapshot() -> None:
    # Journey honours recorded_at <= T; delivery explicitly does NOT offer
    # historical outcomes: it reports the current snapshot state.
    assert "recorded_at <= :report_at" in queries.ENTRY_FUNNEL_SQL
    assert "recorded_at <= :report_at" in queries.RETENTION_SQL_TEMPLATE
    window_from = datetime(2026, 10, 10, tzinfo=UTC)
    report = evaluate_delivery(
        (SyntheticMessage(uuid4(), "match_result", window_from, "sent"),),
        (SyntheticAttempt(uuid4(), "sent", window_from, window_from, window_from),),
        window_from=window_from,
        report_at=T,
    )
    payload = report.as_dict()
    assert payload["historical_outcomes_available"] is False
    assert payload["state_semantics"] == "current_message_and_attempt_state_in_consistent_snapshot"
    validate_delivery_report(payload)


# E · delivery outcomes ---------------------------------------------------------


def test_e1_sent_edited_duplicate_are_known_success_attempts() -> None:
    # Hand: 1 sent message with sent+edited+duplicate attempts -> delivered 1/1
    # at message level; attempts_total 3, unknown 0, waiting 0.
    window_from = datetime(2026, 10, 10, tzinfo=UTC)
    message_id = uuid4()
    messages = (SyntheticMessage(message_id, "match_result", window_from, "sent"),)
    attempts = (
        SyntheticAttempt(message_id, "sent", window_from, window_from, window_from),
        SyntheticAttempt(message_id, "edited", window_from, window_from, window_from),
        SyntheticAttempt(message_id, "duplicate", window_from, window_from, window_from),
    )
    report = evaluate_delivery(messages, attempts, window_from=window_from, report_at=T)
    assert report.messages_total_N == 1
    assert report.messages_delivered_n == 1
    assert report.attempts_total == 3
    assert report.attempts_unknown == 0
    assert report.attempts_waiting == 0
    assert dict(report.attempts_by_outcome) == {"duplicate": 1, "edited": 1, "sent": 1}
    validate_delivery_report(report.as_dict())


def test_e2_retryable_blocked_and_stale_outcomes() -> None:
    # Hand: 4 messages -> delivered 0, failed_known 3 (blocked,
    # chat_unavailable, skip_stale), failed_unknown 1 (unknown_outcome).
    window_from = datetime(2026, 10, 10, tzinfo=UTC)
    blocked, chat, stale, unknown = uuid4(), uuid4(), uuid4(), uuid4()
    messages = (
        SyntheticMessage(blocked, "match_result", window_from, "failed", "blocked"),
        SyntheticMessage(chat, "match_result", window_from, "failed", "chat_unavailable"),
        SyntheticMessage(stale, "match_board", window_from, "failed", "skip_stale"),
        SyntheticMessage(unknown, "match_board", window_from, "failed", "unknown_outcome"),
    )
    attempts = (
        SyntheticAttempt(blocked, "blocked", window_from, window_from, window_from),
        SyntheticAttempt(chat, "chat_unavailable", window_from, window_from, window_from),
        SyntheticAttempt(stale, "skip_stale", window_from, window_from, window_from),
        SyntheticAttempt(unknown, "unknown", window_from, window_from, window_from),
        SyntheticAttempt(blocked, "rate_limited", window_from, window_from, window_from),
        SyntheticAttempt(blocked, "transient", window_from, window_from, window_from),
    )
    report = evaluate_delivery(messages, attempts, window_from=window_from, report_at=T)
    assert report.messages_total_N == 4
    assert report.messages_delivered_n == 0
    assert report.messages_failed_known == 3
    assert report.messages_failed_unknown == 1
    # Unknown message outcome is never promoted to known failure/success.
    assert report.messages_unknown == 1
    assert report.attempts_total == 6
    validate_delivery_report(report.as_dict())


def test_e3_network_started_without_confirmation_stays_waiting() -> None:
    # Hand: sending attempt with network_started_at but finished_at None is
    # in-flight (waiting), not Telegram proof; message stays pending.
    window_from = datetime(2026, 10, 10, tzinfo=UTC)
    message_id = uuid4()
    messages = (SyntheticMessage(message_id, "match_board", window_from, "pending"),)
    attempts = (SyntheticAttempt(message_id, "sending", window_from, window_from, None),)
    report = evaluate_delivery(messages, attempts, window_from=window_from, report_at=T)
    assert (report.messages_total_N, report.messages_pending_waiting) == (1, 1)
    assert report.messages_delivered_n == 0
    assert report.attempts_waiting == 1
    assert report.attempts_unknown == 0


def test_e4_crash_between_send_and_commit_stays_unknown() -> None:
    # Hand: outcome unknown (crash after transport, before commit) ->
    # attempts_unknown 1, message unknown 1, delivered 0, failed_known 0.
    window_from = datetime(2026, 10, 10, tzinfo=UTC)
    message_id = uuid4()
    messages = (
        SyntheticMessage(message_id, "match_board", window_from, "failed", "unknown_outcome"),
    )
    attempts = (SyntheticAttempt(message_id, "unknown", window_from, window_from, window_from),)
    report = evaluate_delivery(messages, attempts, window_from=window_from, report_at=T)
    assert report.messages_failed_unknown == 1
    assert report.messages_failed_known == 0
    assert report.messages_delivered_n == 0
    assert report.messages_unknown == 1
    assert report.attempts_unknown == 1


def test_e5_retry_does_not_double_count_messages() -> None:
    # Hand: transient then sent for one outbox_id -> messages 1/1 delivered,
    # attempts 2. Missing outcomes never become sent/failed.
    window_from = datetime(2026, 10, 10, tzinfo=UTC)
    message_id = uuid4()
    messages = (SyntheticMessage(message_id, "match_result", window_from, "sent"),)
    attempts = (
        SyntheticAttempt(message_id, "transient", window_from, window_from, window_from),
        SyntheticAttempt(
            message_id, "sent", window_from + timedelta(minutes=1), window_from, window_from
        ),
    )
    report = evaluate_delivery(messages, attempts, window_from=window_from, report_at=T)
    assert report.messages_total_N == 1
    assert report.messages_delivered_n == 1
    assert report.attempts_total == 2
    # Delivery SQL keeps unknown via last_error OR an unknown attempt row.
    assert "last_error = 'unknown_outcome'" in queries.DELIVERY_MESSAGES_SQL
    assert "a.outcome = 'unknown'" in queries.DELIVERY_MESSAGES_SQL


# F · empty, zero-mature, validators --------------------------------------------


def test_f1_empty_cohort_and_empty_delivery_window() -> None:
    entry = evaluate_entry_funnel((), (), _coverage())
    assert (entry.starts, entry.eligible, entry.waiting) == (0, 0, 0)
    validate_entry_funnel(entry.as_dict())
    retention = evaluate_retention((), (), _coverage())
    validate_retention_report(retention.as_dict())
    window_from = datetime(2026, 10, 10, tzinfo=UTC)
    delivery = evaluate_delivery((), (), window_from=window_from, report_at=T)
    assert delivery.messages_total_N == 0
    assert delivery.attempts_total == 0
    validate_delivery_report(delivery.as_dict())


def test_f2_zero_mature_n_is_waiting_not_zero_conversion() -> None:
    # Hand: 2 starts after T-24h -> starts 2, eligible 0, waiting 2, n 0.
    managers = (_start(T - timedelta(hours=1)), _start(T - timedelta(hours=2)))
    report = evaluate_entry_funnel(managers, (), _coverage())
    assert (report.starts, report.eligible, report.waiting) == (2, 0, 2)
    assert (report.claimed, report.ready) == (0, 0)
    validate_entry_funnel(report.as_dict())


def test_f3_validators_reject_denominator_substitution() -> None:
    good = evaluate_entry_funnel((_start(START),), (), _coverage()).as_dict()
    validate_entry_funnel(good)
    for bad in (
        dict(good, ready_n=2),
        dict(good, waiting=2),
        dict(good, claimed_n=2),
    ):
        try:
            validate_entry_funnel(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("entry validator must reject mature-denominator swap")
    retention_good = evaluate_retention((_start(START),), (), _coverage()).as_dict()
    try:
        validate_retention_report(dict(retention_good, starts=99))
    except ValueError:
        pass
    else:
        raise AssertionError("retention validator must reject starts swap")


# G · explicitly not implemented -------------------------------------------------


def test_g1_match_opportunity_feedback_and_recovery_have_no_report_api() -> None:
    # These aggregates are planned (measurement-plan / contract-v1) but have
    # no report API or SQL reader yet. The matrix must name them instead of
    # faking emitters to get a green test.
    for missing in (
        "match_opportunity",
        "second_match_return",
        "feedback_summary",
        "error_recovery",
        "match_participation",
    ):
        assert not hasattr(queries, missing), missing
    from goaletto.application import analytics_reports as reports

    for missing in (
        "evaluate_match_opportunity",
        "evaluate_feedback",
        "evaluate_error_recovery",
    ):
        assert not hasattr(reports, missing), missing
