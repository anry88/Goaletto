from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from goaletto.application.analytics import (
    EVENT_SPECS,
    JourneyEvent,
    Origin,
    Reason,
    Surface,
    continues_session,
    match_activity_window,
    retention_window,
    window_is_mature,
)

START = datetime(2026, 10, 11, 12, tzinfo=UTC)


def event(name: str) -> JourneyEvent:
    spec = EVENT_SPECS[name]
    refs = {key: uuid4() for key in spec.required_refs}
    if spec.origin == Origin.USER:
        refs.update(analytics_manager_id=uuid4(), session_id=uuid4())
    return JourneyEvent(
        event_id=uuid4(),
        name=name,
        occurred_at=START,
        recorded_at=START,
        origin=spec.origin,
        operation_id=uuid4(),
        effect_number=0,
        surface=Surface.CLUB,
        **refs,
    )


def test_onboarding_starts_before_club_and_retry_has_the_same_effect_key() -> None:
    started = event("manager_started")
    assert started.club_id is None and started.ownership_id is None
    assert started.counts_as_activity(internal=False)
    assert not started.counts_as_activity(internal=True)
    assert replace(started, event_id=uuid4()).dedupe_key == started.dedupe_key
    assert replace(started, effect_number=1).dedupe_key != started.dedupe_key


def test_delivery_and_simulation_do_not_count_as_reading_or_retention() -> None:
    for name in ("result_delivered", "fixture_finished", "match_command_applied"):
        delivered = event(name)
        assert not delivered.counts_as_activity(internal=False)
        assert not delivered.extends_ownership()
        with pytest.raises(ValueError, match="Background"):
            replace(delivered, session_id=uuid4())
    assert event("match_report_opened").counts_as_activity(internal=False)
    assert event("match_report_opened").extends_ownership()


def test_automation_and_rejections_do_not_renew_ownership() -> None:
    automated = event("bot_youth_replacement_created")
    assert automated.origin == Origin.AUTOMATION
    assert not automated.counts_as_activity(internal=False)
    assert not automated.extends_ownership()
    rejected = JourneyEvent(
        event_id=uuid4(),
        name="action_rejected",
        occurred_at=START,
        recorded_at=START,
        origin=Origin.USER,
        operation_id=uuid4(),
        effect_number=0,
        surface=Surface.SQUAD,
        analytics_manager_id=uuid4(),
        session_id=uuid4(),
        reason=Reason.LINEUP_INVALID,
    )
    assert not rejected.counts_as_activity(internal=False)
    assert not rejected.extends_ownership()


def test_feedback_is_user_activity_but_does_not_renew_club_ownership() -> None:
    response = JourneyEvent(
        event_id=uuid4(),
        name="feedback_answered",
        occurred_at=START,
        recorded_at=START,
        origin=Origin.USER,
        operation_id=uuid4(),
        effect_number=0,
        surface=Surface.FEEDBACK,
        analytics_manager_id=uuid4(),
        session_id=uuid4(),
        feedback_campaign_id=uuid4(),
        reason=Reason.LONG_WAIT,
    )
    assert response.counts_as_activity(internal=False)
    assert not response.extends_ownership()
    assert response.campaign_id is None


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "arbitrary_event"},
        {"version": 2},
        {"version": True},
        {"origin": Origin.AUTOMATION},
        {"origin": Origin.OPERATOR},
        {"analytics_manager_id": None},
        {"session_id": None},
        {"occurred_at": START.replace(tzinfo=None)},
        {"recorded_at": START - timedelta(seconds=1)},
        {"effect_number": True},
        {"effect_number": -1},
        {"reason": Reason.OTHER},
        {"variant": "free arbitrary text"},
    ],
)
def test_invalid_or_unversioned_envelopes_are_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        replace(event("manager_started"), **changes)


def test_no_free_message_payload_is_accepted() -> None:
    with pytest.raises(TypeError):
        replace(event("manager_started"), message="private text")


@pytest.mark.parametrize("name", ["lineup_saved", "match_command_accepted"])
def test_success_needs_a_durable_command_and_ownership(name: str) -> None:
    accepted = event(name)
    for key in ("command_id", "ownership_id", "club_id"):
        with pytest.raises(ValueError, match="reference"):
            replace(accepted, **{key: None})


def test_postponed_match_window_uses_actual_dates_and_closed_window_maturity() -> None:
    actual_start = START + timedelta(days=7)
    actual_finish = actual_start + timedelta(minutes=33)
    window_start, end = match_activity_window(actual_start, actual_finish)
    assert window_start == START + timedelta(days=6)
    assert not window_is_mature(end, end - timedelta(microseconds=1))
    assert window_is_mature(end, end)
    assert window_start <= actual_start < end
    assert not window_start <= end < end  # Upper boundary is excluded.


def test_d7_is_an_exact_window_not_any_return_during_first_week() -> None:
    low, high = retention_window(START, 7)
    assert low == START + timedelta(days=7)
    assert high == START + timedelta(days=8)
    assert not window_is_mature(high, START + timedelta(days=7, hours=23))
    assert window_is_mature(high, START + timedelta(days=8))


def test_session_boundary_is_less_than_thirty_minutes() -> None:
    assert continues_session(START, START + timedelta(minutes=29, seconds=59))
    assert not continues_session(START, START + timedelta(minutes=30))
    assert not continues_session(START, START - timedelta(seconds=1))
