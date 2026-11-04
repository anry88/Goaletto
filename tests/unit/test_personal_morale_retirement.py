"""V8 removes personal morale while preserving crowd support and pinned rules."""

from dataclasses import replace
from uuid import UUID

import pytest

from goaletto.application.match_simulation import demo_match, simulate_match
from goaletto.application.training_staff import POLICY, training_staff
from goaletto.domain.match_codec import restore_state, state_document
from goaletto.domain.match_engine import start_match
from goaletto.domain.match_ratings import sector_ratings
from goaletto.domain.match_types import CoachSnapshot, EnginePolicy, HomeCrowd
from goaletto.domain.training import Focus, Intensity, train_player
from tests.unit.test_knockout_engine import cup_state
from tests.unit.test_training import player


def match(version, morale, crowd=10000):
    initial = demo_match(7)
    teams = [
        replace(
            t.snapshot,
            coach=CoachSnapshot(None),
            players=tuple(replace(p, morale=morale) for p in t.snapshot.players),
        )
        for t in (initial.home, initial.away)
    ]
    return start_match(
        UUID(int=288),
        7,
        *teams,
        engine_version=f"regulation-v{version}",
        policy=EnginePolicy(version=f"match-policy-v{version}"),
        crowd=HomeCrowd(10000, crowd),
    )


def test_v8_personal_morale_changes_neither_ratings_nor_match_events():
    low, high = match(8, 0), match(8, 100)
    assert sector_ratings(low.home, low.policy) == sector_ratings(high.home, high.policy)
    low_end, low_events = simulate_match(low)
    high_end, high_events = simulate_match(high)
    assert low_events == high_events
    assert low_end.home.stats == high_end.home.stats
    assert low_end.away.stats == high_end.away.stats
    assert low_end.home.conditions == high_end.home.conditions
    assert restore_state(state_document(low_end)) == low_end
    # Frozen V7 continues to use the saved personal value.
    old_low, old_high = match(7, 0), match(7, 100)
    assert sector_ratings(old_low.home, old_low.policy) != sector_ratings(
        old_high.home, old_high.policy
    )


def test_v8_keeps_the_neutral_baseline_home_support_and_form_energy():
    current, baseline, empty = match(8, 0), match(7, 50), match(8, 100, crowd=0)
    assert sector_ratings(current.home, current.policy) == sector_ratings(
        baseline.home, baseline.policy
    )
    assert (
        sector_ratings(current.home, current.policy).midfield
        > sector_ratings(empty.home, empty.policy).midfield
    )
    fatigued = replace(
        current.home, conditions=tuple(replace(c, energy_bps=5000) for c in current.home.conditions)
    )
    assert (
        sector_ratings(fatigued, current.policy).midfield
        < sector_ratings(current.home, current.policy).midfield
    )
    poor_form = replace(
        current.home,
        snapshot=replace(
            current.home.snapshot,
            players=tuple(replace(p, form=1) for p in current.home.snapshot.players),
        ),
    )
    assert (
        sector_ratings(poor_form, current.policy).midfield
        < sector_ratings(current.home, current.policy).midfield
    )


def test_v8_neutral_final_extra_time_and_penalties_roundtrip():
    state = cup_state()
    state = replace(
        state,
        engine_version="regulation-v8",
        policy=replace(state.policy, version="match-policy-v8"),
    )
    final, _ = simulate_match(state)
    assert final.tick == 40 and final.knockout.shootout.kicks
    assert final.home.support_morale_points == final.away.support_morale_points == 0
    assert restore_state(state_document(final)) == final


@pytest.mark.parametrize("seed", [str(i) for i in range(30)])
def test_new_training_preserves_morale_and_keeps_all_other_old_gains(seed):
    p = replace(player(), morale=1)
    terms = dict(seed=seed, trainer_skill=20, head_skill=20)
    old = train_player(p, Focus.TEAM, Intensity.MEDIUM, **terms)
    new = train_player(p, Focus.TEAM, Intensity.MEDIUM, personal_morale=False, **terms)
    assert new.morale == p.morale
    assert replace(old, morale=new.morale) == new
    assert new.leadership_gain > 0


def test_new_staff_snapshot_is_explicit_and_old_snapshots_stay_readable():
    assert POLICY == "staff-session-v3"
    assert training_staff({}, Focus.TEAM)["policy"] == POLICY
    assert training_staff({}, Focus.TEAM, policy="staff-session-v2")["policy"] == "staff-session-v2"
