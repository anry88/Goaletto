from collections import Counter
from uuid import UUID

import pytest

from goaletto.domain.candidates import STAFF_ROLES, candidate_profile
from goaletto.domain.match_types import Tactic
from goaletto.domain.staff_quality import generated_staff, quality_tier, staff_abilities


def test_seeded_supply_spans_ages_skills_and_rare_elite():
    values = [generated_staff(UUID(int=n), "head_coach") for n in range(10000)]
    assert values == [generated_staff(UUID(int=n), "head_coach") for n in range(10000)]
    assert {age for age, _ in values} == set(range(50, 57))
    counts = Counter(quality_tier(skill) for _, skill in values)
    assert all(counts[t] > counts[t + 1] for t in range(1, 5))
    assert 50 <= counts[5] <= 160  # v1 design target: 1%, broad deterministic tolerance
    assert {skill for _, skill in values} == set(range(1, 21))


@pytest.mark.parametrize("role", STAFF_ROLES)
def test_capabilities_are_role_specific_and_generation_is_separate_from_money(role):
    name, age, skills, fee, wage = candidate_profile(UUID(int=12), "staff", role)
    assert name and 50 <= age <= 56 and fee == 4 * wage
    for skill in (1, 4, 5, 8, 9, 12, 13, 16, 17, 20):
        abilities = staff_abilities(role, skill)
        assert abilities.tier == quality_tier(skill)
        assert bool(abilities.tactics) == (role == "head_coach")
        assert bool(abilities.scouting) == (role == "scout")
        assert bool(abilities.recovery) == (role in {"physiotherapist", "fitness_coach"})
        assert bool(abilities.morale) == (role == "head_coach")
        if role in {"scout", "physiotherapist"}:
            assert not abilities.training
    beginner = staff_abilities("head_coach", 1)
    assert beginner.tactics == (Tactic.NORMAL,)
    assert (beginner.instruction_changes, beginner.mentality_changes) == (1, 1)
    assert set(staff_abilities("head_coach", 20).tactics) == set(Tactic)


def test_reject_unknown_profile_and_policy():
    with pytest.raises(ValueError):
        staff_abilities("physiotherapist", 20, "future")
    with pytest.raises(ValueError):
        quality_tier(True)
