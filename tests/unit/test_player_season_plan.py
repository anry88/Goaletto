"""Pure-function tests for domain.player_season_plan. No DB, no IO."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from goaletto.domain.player_season_plan import PlayerSeasonEntry, plan_player_season
from goaletto.domain.retirement import PLAYER_RETIREMENT_POLICY, PROBABILITIES_BPS

COMPLETED = UUID("11111111-1111-4111-8111-111111111111")
NEXT = UUID("22222222-2222-4222-8222-222222222222")
OTHER_SEASON = UUID("33333333-3333-4333-8333-333333333333")


def make_entry(
    *,
    age: int,
    status: str = "active",
    draw: int = 9999,
    last: UUID | None = None,
    retired_after: UUID | None = None,
    applied: UUID | None = COMPLETED,
) -> PlayerSeasonEntry:
    return PlayerSeasonEntry(
        person_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        appearance_identity_id="player-0001",
        completed_season_id=COMPLETED,
        next_season_id=NEXT,
        current_age=age,
        current_age_applied_for_season_id=applied,
        current_status=status,  # type: ignore[arg-type]
        current_last_season_id=last,
        current_retired_after_season_id=retired_after,
        draw=draw,
    )


def test_age_31_to_32_uses_five_percent_scale() -> None:
    plan = plan_player_season(make_entry(age=31, draw=499))
    assert plan.outcome == "continue_last_season"
    assert plan.next_age == 32
    assert plan.next_status == "last_season"
    assert plan.next_last_season_id == NEXT
    assert plan.next_age_applied_for_season_id == NEXT
    assert plan.decision is not None
    assert plan.decision.age == 32
    assert plan.decision.probability_bps == 500
    assert plan.decision.draw == 499
    assert plan.decision.policy_version == PLAYER_RETIREMENT_POLICY

    calm = plan_player_season(make_entry(age=31, draw=500))
    assert calm.outcome == "continue_active"
    assert calm.next_age == 32
    assert calm.next_status == "active"
    assert calm.next_last_season_id is None
    assert calm.decision is not None
    assert calm.decision.draw == 500  # probabilistic draws are stored win or lose
    assert not calm.decision.is_last_season


def test_age_37_to_38_is_always_a_last_season() -> None:
    for draw in (0, 1234, 9999):
        plan = plan_player_season(make_entry(age=37, draw=draw))
        assert plan.outcome == "continue_last_season"
        assert plan.next_age == 38
        assert plan.next_status == "last_season"
        assert plan.decision is not None
        assert plan.decision.probability_bps == 10000
        assert plan.decision.draw is None
        assert plan.decision.is_last_season


@pytest.mark.parametrize(
    "age,probability", list(zip(range(32, 39), PROBABILITIES_BPS, strict=True))
)
def test_probability_endpoints_for_every_warning_age(age: int, probability: int) -> None:
    # Hand-counted via the approved scale: draw runs 0..9999, warn iff draw < probability.
    below = plan_player_season(make_entry(age=age - 1, draw=probability - 1))
    assert below.next_age == age
    assert below.outcome == "continue_last_season"
    assert below.decision is not None and below.decision.is_last_season
    if probability < 10000:
        above = plan_player_season(make_entry(age=age - 1, draw=probability))
        assert above.next_age == age
        assert above.outcome == "continue_active"
        assert above.decision is not None and not above.decision.is_last_season


@pytest.mark.parametrize("age", range(17, 31))
def test_young_players_age_without_warning(age: int) -> None:
    plan = plan_player_season(make_entry(age=age, draw=0))
    assert plan.outcome == "continue_active"
    assert plan.next_age == age + 1
    assert plan.decision is not None
    assert plan.decision.probability_bps == 0
    assert plan.decision.draw is None
    assert not plan.decision.is_last_season


def test_warned_season_is_followed_by_retirement() -> None:
    entry = make_entry(age=34, status="last_season", last=COMPLETED, draw=0)
    plan = plan_player_season(entry)
    assert plan.outcome == "retire_now"
    assert plan.next_status == "retired"
    assert plan.next_last_season_id == COMPLETED
    assert plan.next_retired_after_season_id == COMPLETED
    assert plan.next_age == 34  # last played age preserved, no new aging
    assert plan.next_age_applied_for_season_id == COMPLETED
    assert plan.decision is None  # no new draw at retirement
    assert plan.appearance_identity_id == "player-0001"


def test_announcement_is_not_retirement_yet() -> None:
    plan = plan_player_season(make_entry(age=31, draw=0))
    assert plan.outcome == "continue_last_season"
    assert plan.next_status == "last_season"
    assert plan.next_retired_after_season_id is None


def test_retry_with_different_draw_keeps_accepted_plan() -> None:
    entry = make_entry(age=31, draw=10)
    accepted = plan_player_season(entry)
    assert accepted.outcome == "continue_last_season"

    retry_other_draw = replace(entry, draw=9876)
    assert plan_player_season(retry_other_draw, prior=accepted) is accepted

    # Already-applied post-image (aged row, flag on the next season) also replays.
    applied = PlayerSeasonEntry(
        person_id=entry.person_id,
        appearance_identity_id=entry.appearance_identity_id,
        completed_season_id=COMPLETED,
        next_season_id=NEXT,
        current_age=accepted.next_age,
        current_age_applied_for_season_id=NEXT,
        current_status="last_season",
        current_last_season_id=NEXT,
        current_retired_after_season_id=None,
        draw=5555,
    )
    assert plan_player_season(applied, prior=accepted) is accepted


def test_retry_of_retirement_plan_is_stable() -> None:
    entry = make_entry(age=36, status="last_season", last=COMPLETED, draw=0)
    accepted = plan_player_season(entry)
    assert accepted.outcome == "retire_now"
    assert plan_player_season(entry, prior=accepted) is accepted

    applied = PlayerSeasonEntry(
        person_id=entry.person_id,
        appearance_identity_id=entry.appearance_identity_id,
        completed_season_id=COMPLETED,
        next_season_id=NEXT,
        current_age=36,
        current_age_applied_for_season_id=COMPLETED,
        current_status="retired",
        current_last_season_id=COMPLETED,
        current_retired_after_season_id=COMPLETED,
        draw=777,
    )
    assert plan_player_season(applied, prior=accepted) is accepted


def test_retired_player_never_ages_or_returns() -> None:
    entry = make_entry(age=38, status="retired", last=COMPLETED, retired_after=COMPLETED, draw=0)
    plan = plan_player_season(entry)
    assert plan.outcome == "already_retired"
    assert plan.next_age == 38
    assert plan.next_status == "retired"
    assert plan.next_age_applied_for_season_id is None
    assert plan.decision is None


def test_foreign_receipt_or_season_is_rejected() -> None:
    entry = make_entry(age=31, draw=10)
    accepted = plan_player_season(entry)
    stranger = replace(entry, person_id=uuid4())
    with pytest.raises(ValueError):
        plan_player_season(stranger, prior=accepted)
    with pytest.raises(ValueError):
        plan_player_season(entry, prior=replace(accepted, completed_season_id=OTHER_SEASON))
    with pytest.raises(ValueError):
        plan_player_season(entry, prior=replace(accepted, next_season_id=OTHER_SEASON))
    with pytest.raises(ValueError):
        plan_player_season(entry, prior=replace(accepted, appearance_identity_id="player-0002"))
    # State matching neither the accepted pre-image nor the applied post-image.
    corrupted = make_entry(age=29, draw=10)
    with pytest.raises(ValueError):
        plan_player_season(corrupted, prior=accepted)


def test_owned_and_unowned_clubs_share_the_same_plan() -> None:
    # Ownership is not an input: the same footballer row plans identically.
    assert plan_player_season(make_entry(age=30, draw=42)) == plan_player_season(
        make_entry(age=30, draw=42)
    )


def test_identity_is_preserved() -> None:
    plan = plan_player_season(make_entry(age=25, draw=7))
    assert plan.person_id == UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    assert plan.appearance_identity_id == "player-0001"


@pytest.mark.parametrize(
    "entry",
    [
        make_entry(age=38, draw=0),  # active 38 is impossible: 38 is always warned
        make_entry(age=16, draw=0),
        make_entry(age=39, draw=0),
        make_entry(age=True, draw=0),  # type: ignore[arg-type]
        make_entry(age=30, draw=-1),
        make_entry(age=30, draw=10000),
        make_entry(age=30, draw=True),  # type: ignore[arg-type]
        make_entry(age=30, draw=0, applied=None),
        make_entry(age=30, draw=0, status="active", last=COMPLETED),
        make_entry(age=34, draw=0, status="last_season", last=None),
        make_entry(age=34, draw=0, status="last_season", last=COMPLETED, retired_after=COMPLETED),
        make_entry(age=34, draw=0, status="last_season", last=OTHER_SEASON),
        make_entry(age=34, draw=0, status="retired", last=COMPLETED, retired_after=OTHER_SEASON),
        make_entry(age=30, draw=0, applied=NEXT, status="active"),  # post-image w/o receipt
    ],
)
def test_invalid_inputs_raise(entry: PlayerSeasonEntry) -> None:
    with pytest.raises(ValueError):
        plan_player_season(entry)


def test_identical_seasons_raise() -> None:
    entry = make_entry(age=30, draw=0)
    same = replace(entry, next_season_id=COMPLETED)
    with pytest.raises(ValueError):
        plan_player_season(same)


def test_retry_rejects_non_boolean_decision_outcome() -> None:
    entry = make_entry(age=30)
    prior = plan_player_season(entry)
    assert prior.decision is not None
    corrupt = replace(prior, decision=replace(prior.decision, is_last_season=0))
    with pytest.raises(ValueError, match="boolean"):
        plan_player_season(entry, prior=corrupt)
