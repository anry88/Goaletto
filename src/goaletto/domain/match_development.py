"""Modest appearance development, sharing the training age-season budget."""

from dataclasses import dataclass

from goaletto.domain.development import GrowthBudget, SkillGrowth, grant_growth
from goaletto.domain.training import PRIMARY_SKILLS, TrainingPlayer, age_multiplier

POLICY = "match-development-v1"
POSITION_ROLES = {
    position: (
        "goalkeeping_coach"
        if position == "GK"
        else "defence_coach"
        if position in {"DC", "DL", "DR", "DM"}
        else "attack_coach"
    )
    for position in PRIMARY_SKILLS
}


@dataclass(frozen=True)
class AppearanceGrowth:
    growth: SkillGrowth
    experience: int
    experience_gain: int


def develop_appearance(
    player: TrainingPlayer, minutes: int, staff: dict[str, int], budget: GrowthBudget
) -> AppearanceGrowth:
    if type(minutes) is not int or not 0 <= minutes <= 90 or budget.age != player.age:
        raise ValueError("Invalid appearance minutes or age")
    if any(type(v) is not int or not 1 <= v <= 20 for v in staff.values()):
        raise ValueError("Invalid match staff")
    specialist = staff.get(POSITION_ROLES[player.position], 0)
    strength = 5000 + specialist * 500 if specialist else 3000 + staff.get("head_coach", 0) * 100
    gain = 240 * age_multiplier(player.age) * strength * minutes // (10000**2 * 90)
    result = grant_growth(
        player.skills,
        player.caps,
        player.progress,
        budget,
        {PRIMARY_SKILLS[player.position]: gain},
        source="match",
    )
    fractions = dict(result.progress)
    experience_gain = 1000 * age_multiplier(player.age) * minutes // (10000 * 90)
    whole, fractions["experience"] = divmod(fractions["experience"] + experience_gain, 10000)
    result = SkillGrowth(result.skills, fractions, result.budget, result.granted)
    return AppearanceGrowth(result, player.experience + whole, experience_gain)
