"""Actual positions drive exposure, skill profiles and experience in V5 only."""

from dataclasses import replace
from uuid import uuid4

import pytest

from goaletto.application.match_simulation import demo_match
from goaletto.domain.development import GrowthBudget
from goaletto.domain.match_codec import restore_state, state_document, state_hash
from goaletto.domain.match_engine import advance_tick, start_match, validate_state
from goaletto.domain.match_practice import (
    CURRENT_POLICY,
    DEFAULT_FRIENDLY_EXPERIENCE_FACTOR,
    DEFAULT_FRIENDLY_SKILL_FACTOR,
    V3_POLICY,
    develop_match_practice,
    develop_position_practice,
)
from goaletto.domain.match_ratings import effective_skill
from goaletto.domain.match_types import (
    CoachSnapshot,
    CommandKind,
    EnginePolicy,
    FieldSlot,
    MatchCommand,
    PlayerCondition,
    Side,
)
from goaletto.domain.position_experience import credit_experience, experience_bonus
from goaletto.domain.training import Focus, available_focus
from tests.unit.test_match_practice import make_player


def test_actual_positions_choose_skills_and_specialists_not_natural_position():
    player = make_player(18, position="DC")
    staff = {"midfield_coach": 10, "attack_coach": 20, "defence_coach": 1}
    result = develop_position_practice(
        player, "league", {"MC": 60, "FC": 30}, staff, GrowthBudget(18), match_seed="positions"
    )
    assert result.granted["playmaking"] > 0 and result.granted["attacking"] > 0
    assert result.granted["defending"] == 0
    assert result.experience_by_position == {"MC": 960, "FC": 480}
    assert {row["position"]: row["role"] for row in result.position_terms} == {
        "MC": "midfield_coach",
        "FC": "attack_coach",
    }
    stronger = develop_position_practice(
        player,
        "league",
        {"MC": 90},
        {"midfield_coach": 20},
        GrowthBudget(18),
        match_seed="positions",
    )
    weaker = develop_position_practice(
        player,
        "league",
        {"MC": 90},
        {"midfield_coach": 1, "attack_coach": 20},
        GrowthBudget(18),
        match_seed="positions",
    )
    older = develop_position_practice(
        make_player(30),
        "league",
        {"MC": 90},
        {"midfield_coach": 20},
        GrowthBudget(30),
        match_seed="positions",
    )
    assert stronger.granted["playmaking"] > weaker.granted["playmaking"]
    assert stronger.granted["playmaking"] > older.granted["playmaking"]


def test_friendlies_grant_75_percent_and_frozen_v3_retains_50_percent():
    player = make_player(22)
    official = develop_position_practice(
        player, "league", {"MC": 90}, {"midfield_coach": 10}, GrowthBudget(22), match_seed="ratio"
    )
    friendly = develop_position_practice(
        player, "friendly", {"MC": 90}, {"midfield_coach": 10}, GrowthBudget(22), match_seed="ratio"
    )
    assert friendly.terms.policy == CURRENT_POLICY
    assert friendly.terms.skill_factor == DEFAULT_FRIENDLY_SKILL_FACTOR == 7500
    assert friendly.terms.experience_factor == DEFAULT_FRIENDLY_EXPERIENCE_FACTOR == 7500
    assert friendly.granted["playmaking"] == official.granted["playmaking"] * 3 // 4
    assert friendly.experience_gain == official.experience_gain * 3 // 4
    assert (friendly.form, friendly.form_draw) == (official.form, official.form_draw)
    frozen = develop_match_practice(
        player,
        "friendly",
        90,
        {"attack_coach": 10},
        GrowthBudget(22),
        match_seed="ratio",
        policy=V3_POLICY,
    )
    assert frozen.terms.skill_factor == frozen.terms.experience_factor == 5000


def test_multiple_roles_share_one_growth_budget_and_zero_minutes_preserve_fractions():
    player = make_player(18)
    used = dict(GrowthBudget(18).used)
    used.update(playmaking=9999, match=9999)
    result = develop_position_practice(
        player,
        "cup",
        {"MC": 60, "FC": 60},
        {"midfield_coach": 20, "attack_coach": 20},
        GrowthBudget(18, used),
        match_seed="caps",
    )
    assert sum(result.granted.values()) == 1
    assert result.growth.budget.used["match"] == 10000
    assert result.experience_by_position == {"MC": 960, "FC": 960}
    capped = replace(
        player,
        caps=dict(player.skills),
        progress={**player.progress, "playmaking": 123, "experience": 234},
    )
    unused = develop_position_practice(capped, "cup", {}, {}, GrowthBudget(18), match_seed="zero")
    assert unused.progress == capped.progress and unused.skills == capped.skills
    assert unused.experience == capped.experience and unused.form == capped.form


@pytest.mark.parametrize(
    "position,skill",
    [
        ("GK", "goalkeeping"),
        ("DC", "defending"),
        ("MC", "playmaking"),
        ("ML", "winger"),
        ("FC", "attacking"),
    ],
)
def test_experience_improves_only_the_position_played_for_every_role(position, skill):
    player = replace(
        demo_match().home.snapshot.players[0],
        position=position,
        position_experience=((position, 500000),),
    )
    condition = PlayerCondition(player.id)
    novice = replace(player, position_experience=())
    trained = effective_skill(player, condition, position, skill, positional=True)
    basic = effective_skill(novice, condition, position, skill, positional=True)
    assert trained > basic
    other = "FC" if position != "FC" else "MC"
    assert effective_skill(player, condition, other, skill, positional=True) == effective_skill(
        novice, condition, other, skill, positional=True
    )
    assert effective_skill(player, condition, position, skill) == effective_skill(
        novice, condition, position, skill
    )
    assert experience_bonus(1000000) == experience_bonus(2000000) == 1000


def test_v5_counts_actual_position_minutes_and_substitutes_and_round_trips():
    source = demo_match()
    snapshots = [
        replace(team.snapshot, coach=CoachSnapshot(None)) for team in (source.home, source.away)
    ]
    policy = EnginePolicy(
        version="match-policy-v5",
        base_chance_rate_bps=0,
        yellow_bps=0,
        direct_red_bps=0,
        injury_bps=0,
        foul_bps=0,
    )
    state = start_match(uuid4(), 1, *snapshots, engine_version="regulation-v5", policy=policy)
    state = advance_tick(state, 1).state
    mc = next(slot for slot in state.home.lineup if slot.position == "MC")
    fc = next(slot for slot in state.home.lineup if slot.position == "FC")
    moved = tuple(
        FieldSlot(slot.player_id, "FC" if slot == mc else "MC" if slot == fc else slot.position)
        for slot in state.home.lineup
    )
    state = replace(state, home=replace(state.home, lineup=moved))
    state = advance_tick(state, 2).state
    condition = next(c for c in state.home.conditions if c.player_id == mc.player_id)
    assert condition.position_minutes == (("MC", 3), ("FC", 3))
    incoming = state.home.bench[0]
    command = MatchCommand(
        uuid4(),
        Side.HOME,
        3,
        CommandKind.SUBSTITUTION,
        outgoing_id=mc.player_id,
        incoming_id=incoming,
    )
    state = advance_tick(state, 3, commands=(command,)).state
    positions = {c.player_id: dict(c.position_minutes) for c in state.home.conditions}
    assert positions[mc.player_id] == {"MC": 3, "FC": 3}
    assert positions[incoming] == {"FC": 3}
    assert restore_state(state_document(state)) == state
    assert state_hash(restore_state(state_document(state))) == state_hash(state)
    broken = replace(condition, position_minutes=(("MC", 1),))
    with pytest.raises(ValueError, match="reconcile"):
        validate_state(
            replace(
                state,
                home=replace(
                    state.home,
                    conditions=tuple(
                        broken if c.player_id == broken.player_id else c
                        for c in state.home.conditions
                    ),
                ),
            )
        )


def test_new_trainers_unlock_only_their_profiles_and_experience_credits_are_pure():
    assert available_focus(Focus.PLAYMAKING, {"midfield_coach": 1})
    assert available_focus(Focus.WINGER, {"wing_coach": 1})
    assert not available_focus(Focus.PLAYMAKING, {"attack_coach": 20})
    assert not available_focus(Focus.WINGER, {"attack_coach": 20})
    assert available_focus(Focus.PLAYMAKING, {"attack_coach": 20}, legacy=True)
    before = {"MC": 1000}
    assert credit_experience(before, {"FC": 1200, "MC": 400}) == {"MC": 1400, "FC": 1200}
    assert before == {"MC": 1000}
