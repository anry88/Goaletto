from dataclasses import replace
from uuid import UUID

import pytest

from goaletto.application.match_simulation import demo_match, simulate_match
from goaletto.domain.development import GrowthBudget
from goaletto.domain.match_codec import restore_state, state_document, state_hash
from goaletto.domain.match_engine import start_match
from goaletto.domain.match_practice import develop_percent_practice
from goaletto.domain.match_ratings import effective_skill
from goaletto.domain.match_types import CoachSnapshot, EnginePolicy, PlayerCondition
from goaletto.domain.position_experience import percent_practice, validate_percent_experience
from tests.unit.test_match_practice import make_player


def test_each_unused_position_decays_even_if_another_position_was_played():
    before = {"FC": 400000, "MC": 300000, "DC": 200000}
    after, gains, losses = percent_practice(before, {"MC": 1500}, ("FC", "AMC"), age=17, decay=True)
    assert gains == {"MC": 15000} and losses == {"FC": 15000, "DC": 15000}
    assert after == {"FC": 385000, "MC": 315000, "DC": 185000} and before == {
        "FC": 400000,
        "MC": 300000,
        "DC": 200000,
    }
    capped, gain, _ = percent_practice({"FC": 990000}, {"FC": 1500}, ("FC",), age=17)
    assert capped == {"FC": 1000000} and gain == {"FC": 10000}
    assert percent_practice({}, {}, ("FC",), age=17, decay=True) == ({}, {}, {})


def test_nonappearance_decay_is_tenfold_smaller_and_decreases_with_age():
    _, _, young = percent_practice({"FC": 100000}, {}, ("FC",), age=17, decay=True)
    _, _, old = percent_practice({"FC": 100000}, {}, ("FC",), age=38, decay=True)
    assert young == {"FC": 15000} and old == {"FC": 3000}
    assert percent_practice({"FC": 5}, {}, ("FC",), age=38, decay=True)[0] == {"FC": 0}
    for invalid in ({"FC": 1000001}, {"FC": -1}, {"unknown": 1}):
        with pytest.raises(ValueError):
            validate_percent_experience(invalid)


def test_declared_positions_learn_normally_foreign_positions_only_ten_percent():
    p = replace(
        make_player(17, position="FC"),
        profile_positions=("FC", "AMC"),
        position_experience={"FC": 400000, "AMC": 50000, "DC": 100000},
    )
    own = develop_percent_practice(p, "league", {"FC": 90}, {}, GrowthBudget(17), match_seed="xp")
    alt = develop_percent_practice(p, "league", {"AMC": 90}, {}, GrowthBudget(17), match_seed="xp")
    foreign = develop_percent_practice(
        p, "league", {"DC": 90}, {}, GrowthBudget(17), match_seed="xp"
    )
    assert own.experience_by_position == {"FC": 150000}
    assert alt.experience_by_position == {"AMC": 150000}
    assert foreign.experience_by_position == {"DC": 15000}
    assert foreign.experience_losses == {"FC": 15000, "AMC": 15000}
    rested = develop_percent_practice(p, "league", {}, {}, GrowthBudget(17), match_seed="xp")
    assert rested.experience_gain == 0 and rested.granted == dict.fromkeys(p.skills, 0)
    assert rested.position_experience_after == {"FC": 385000, "AMC": 35000, "DC": 85000}
    friendly = develop_percent_practice(
        p, "friendly", {"DC": 90}, {}, GrowthBudget(17), match_seed="xp"
    )
    assert friendly.experience_by_position["DC"] == 11250
    assert friendly.experience_losses["FC"] == 11250


def test_effectiveness_uses_only_earned_percent_without_fixed_position_factors():
    source = demo_match().home.snapshot.players[0]
    p = replace(
        source,
        position="FC",
        alternatives=("AMC",),
        position_experience=(("FC", 400000), ("AMC", 300000), ("DC", 200000)),
    )
    c = PlayerCondition(p.id)
    scores = [
        effective_skill(p, c, pos, "attacking", percentage=True) for pos in ("FC", "AMC", "DC")
    ]
    assert scores[0] > scores[1] > scores[2] > 0
    assert effective_skill(p, c, "ML", "attacking", percentage=True) == 0
    assert scores[0] == scores[2] * 2


def test_v6_roundtrip_and_new_zero_experience_world_runs_without_synthetic_percent():
    source = demo_match()
    snapshots = [replace(t.snapshot, coach=CoachSnapshot(None)) for t in (source.home, source.away)]
    state = start_match(
        UUID(int=123),
        42,
        *snapshots,
        engine_version="regulation-v6",
        policy=EnginePolicy(version="match-policy-v6"),
    )
    assert all(
        not p.position_experience for t in (state.home, state.away) for p in t.snapshot.players
    )
    end, _ = simulate_match(state)
    assert restore_state(state_document(end)) == end
    assert state_hash(restore_state(state_document(end))) == state_hash(end)
    assert all(
        sum(dict(c.position_minutes).values()) == c.minutes_played
        for t in (end.home, end.away)
        for c in t.conditions
    )
