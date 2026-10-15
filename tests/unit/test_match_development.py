"""Appearance minutes, independent experience and the shared bounded budget."""

from dataclasses import replace
from uuid import UUID

import pytest

from goaletto.domain.candidates import STAFF_ROLES
from goaletto.domain.development import GrowthBudget
from goaletto.domain.match_development import POSITION_ROLES, develop_appearance
from goaletto.domain.staff_career import loyal_wage, retirement_career
from goaletto.domain.staff_quality import quality_tier
from tests.unit.test_training import player


@pytest.mark.parametrize("position", tuple(POSITION_ROLES))
def test_minutes_and_only_corresponding_staff_scale_growth(position):
    p = player(18, position)
    none = develop_appearance(p, 0, {}, GrowthBudget(18))
    assert not any(none.growth.granted.values()) and none.experience_gain == 0
    role = POSITION_ROLES[position]
    full = develop_appearance(p, 90, {role: 20}, GrowthBudget(18))
    partial = develop_appearance(p, 30, {role: 20}, GrowthBudget(18))
    weak = develop_appearance(p, 90, {role: 1}, GrowthBudget(18))
    irrelevant = develop_appearance(
        p, 90, {"physiotherapist": 20, "fitness_coach": 20, "scout": 20}, GrowthBudget(18)
    )
    assert 0 < sum(partial.growth.granted.values()) < sum(full.growth.granted.values())
    assert sum(weak.growth.granted.values()) < sum(full.growth.granted.values())
    assert irrelevant == develop_appearance(p, 90, {}, GrowthBudget(18))
    assert weak.experience_gain == full.experience_gain
    assert full == develop_appearance(p, 90, {role: 20}, GrowthBudget(18))
    assert sum(
        develop_appearance(
            player(30, position), 90, {role: 20}, GrowthBudget(30)
        ).growth.granted.values()
    ) < sum(full.growth.granted.values())


def test_capped_skill_still_accumulates_experience_without_changing_other_attributes():
    p = player(21)
    p = replace(p, caps=dict(p.skills), progress={**p.progress, "experience": 9999})
    result = develop_appearance(p, 90, {"attack_coach": 20}, GrowthBudget(21))
    assert result.growth.skills == p.skills and result.experience == p.experience + 1
    assert not any(result.growth.granted.values())
    assert p.form == 6 and p.morale == 50 and p.progress["experience"] == 9999
    with pytest.raises(ValueError):
        develop_appearance(p, 91, {}, GrowthBudget(21))
    with pytest.raises(ValueError):
        develop_appearance(p, True, {}, GrowthBudget(21))


def test_retirement_career_is_stable_independent_of_ovr_and_has_rare_elite_staff():
    season = UUID(int=400)
    outcomes = [retirement_career(UUID(int=i), season) for i in range(1, 10001)]
    assert all(1 <= skill <= 20 for _, skill in outcomes)
    assert set(role for role, _ in outcomes) == set(STAFF_ROLES)
    assert sum(quality_tier(skill) == 5 for _, skill in outcomes) < 200
    assert sum(quality_tier(skill) >= 4 for _, skill in outcomes) < 1000
    assert retirement_career(UUID(int=123), season) == retirement_career(UUID(int=123), season)
    assert loyal_wage(101) == 80 and loyal_wage(100) == 80
