from dataclasses import replace
from uuid import UUID

import pytest

from goaletto.application.match_simulation import demo_match, simulate_match
from goaletto.domain.match_codec import restore_state, state_document, state_hash
from goaletto.domain.match_engine import start_match
from goaletto.domain.match_ratings import effective_skill, sector_ratings
from goaletto.domain.match_types import CoachSnapshot, EnginePolicy, PlayerCondition
from goaletto.domain.position_experience import bounded_experience_factor
from tests.unit.test_knockout_engine import cup_state


@pytest.mark.parametrize(
    "xp,factor",
    [(0, 800000), (250000, 850000), (500000, 900000), (750000, 950000), (1000000, 1000000)],
)
def test_exact_continuous_effect_and_all_positions_share_it(xp, factor):
    assert bounded_experience_factor(xp) == factor
    p = replace(
        demo_match().home.snapshot.players[0],
        position="DC",
        alternatives=("DR",),
        position_experience=tuple((pos, xp) for pos in ("DC", "DR", "FC")),
    )
    full = replace(p, position_experience=tuple((pos, 1000000) for pos in ("DC", "DR", "FC")))
    for pos in ("DC", "DR", "FC"):
        condition = PlayerCondition(p.id)
        expected = effective_skill(full, condition, pos, "defending", percentage=True)
        actual = effective_skill(
            p, condition, pos, "defending", percentage=True, bounded_experience=True
        )
        assert abs(actual - expected * factor // 1000000) <= 1
    assert (
        effective_skill(
            replace(p, position_experience=()),
            PlayerCondition(p.id),
            "MC",
            "defending",
            percentage=True,
            bounded_experience=True,
        )
        > 0
    )


def test_near_and_foreign_positions_use_relevant_skills_not_fixed_role_penalties():
    p = demo_match().home.snapshot.players[0]
    p = replace(
        p,
        position="DC",
        alternatives=("DR",),
        skills=replace(p.skills, defending=10, attacking=2),
        position_experience=(),
    )
    c = PlayerCondition(p.id)
    defence = effective_skill(p, c, "DR", "defending", percentage=True, bounded_experience=True)
    attack = effective_skill(p, c, "FC", "attacking", percentage=True, bounded_experience=True)
    assert abs(defence - 5 * attack) <= 4
    assert effective_skill(p, c, "DC", "defending", percentage=True) == 0  # frozen V6


@pytest.mark.parametrize("invalid", [-1, 1000001, True, 0.5])
def test_invalid_earned_experience_is_rejected(invalid):
    with pytest.raises(ValueError):
        bounded_experience_factor(invalid)


@pytest.mark.parametrize("version", [6, 7])
def test_zero_experience_regulation_and_knockout_roundtrip(version):
    initial = demo_match()
    teams = [replace(t.snapshot, coach=CoachSnapshot(None)) for t in (initial.home, initial.away)]
    st = start_match(
        UUID(int=723),
        42,
        *teams,
        engine_version=f"regulation-v{version}",
        policy=EnginePolicy(version=f"match-policy-v{version}"),
    )
    keeper = sector_ratings(st.home, st.policy).goalkeeper
    assert (keeper > 0) == (version == 7)
    end, _ = simulate_match(st)
    assert restore_state(state_document(end)) == end
    assert state_hash(restore_state(state_document(end))) == state_hash(end)
    cup = cup_state()
    cup = replace(
        cup,
        engine_version=f"regulation-v{version}",
        policy=replace(cup.policy, version=f"match-policy-v{version}"),
    )
    end, events = simulate_match(cup)
    assert end.tick == 40 and len(end.knockout.shootout.kicks) >= 6
    assert restore_state(state_document(end)) == end
    # At zero experience V7 ranks penalty takers by their actual set-piece skill.
    kick = next(e for e in events if e.kind in ("penalty_scored", "penalty_missed"))
    team = cup.home if kick.side.value == "home" else cup.away
    if version == 7:
        p = next(p for p in team.snapshot.players if p.id == kick.player_id)
        active = {s.player_id for s in team.lineup}
        assert p.skills.set_pieces == max(
            p.skills.set_pieces for p in team.snapshot.players if p.id in active
        )
