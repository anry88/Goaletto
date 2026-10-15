"""Shared age-season growth budget for training and future match development."""

from dataclasses import dataclass, field

SKILLS = (
    "goalkeeping",
    "defending",
    "playmaking",
    "winger",
    "attacking",
    "set_pieces",
    "stamina",
    "pace",
)
BUDGET_KEYS = (*SKILLS, "training", "match", "leadership")
TOTAL_LIMIT = 30_000
SKILL_LIMIT = 15_000
SOURCE_LIMITS = {"training": 20_000, "match": 10_000}
LEADERSHIP_LIMIT = 5_000
GROWTH_POLICY = "development-budget-v1"


@dataclass(frozen=True)
class GrowthBudget:
    age: int
    used: dict[str, int] = field(default_factory=lambda: dict.fromkeys(BUDGET_KEYS, 0))

    def __post_init__(self) -> None:
        if type(self.age) is not int or not 17 <= self.age <= 150:
            raise ValueError("Invalid development age")
        if set(self.used) != set(BUDGET_KEYS) or any(
            type(v) is not int or v < 0 for v in self.used.values()
        ):
            raise ValueError("Invalid growth accounting")
        total = sum(self.used[k] for k in SKILLS)
        if total != self.used["training"] + self.used["match"] or total > TOTAL_LIMIT:
            raise ValueError("Growth totals disagree")
        if (
            any(self.used[k] > SKILL_LIMIT for k in SKILLS)
            or any(self.used[k] > limit for k, limit in SOURCE_LIMITS.items())
            or self.used["leadership"] > LEADERSHIP_LIMIT
        ):
            raise ValueError("Seasonal growth limit exceeded")


@dataclass(frozen=True)
class SkillGrowth:
    skills: dict[str, int]
    progress: dict[str, int]
    budget: GrowthBudget
    granted: dict[str, int]


def grant_growth(
    skills: dict[str, int],
    caps: dict[str, int],
    progress: dict[str, int],
    budget: GrowthBudget,
    requested: dict[str, int],
    *,
    source: str,
) -> SkillGrowth:
    if (
        source not in SOURCE_LIMITS
        or not set(requested) <= set(SKILLS)
        or any(type(v) is not int or v < 0 for v in requested.values())
    ):
        raise ValueError("Invalid development grant")
    if set(skills) != set(SKILLS) or set(caps) != set(SKILLS) or not set(SKILLS) <= set(progress):
        raise ValueError("Invalid development attributes")
    if any(
        type(skills[k]) is not int
        or type(caps[k]) is not int
        or not 1 <= skills[k] <= caps[k] <= 20
        or type(progress[k]) is not int
        or not 0 <= progress[k] < 10000
        for k in SKILLS
    ):
        raise ValueError("Invalid development potential/progress")
    values, fractions, used, granted = dict(skills), dict(progress), dict(budget.used), {}
    for key in SKILLS:
        requested_gain = requested.get(key, 0)
        room = (caps[key] - values[key]) * 10000 - fractions[key]
        gain = max(
            0,
            min(
                requested_gain,
                room,
                TOTAL_LIMIT - sum(used[k] for k in SKILLS),
                SOURCE_LIMITS[source] - used[source],
                SKILL_LIMIT - used[key],
            ),
        )
        used[key] += gain
        used[source] += gain
        whole, fraction = divmod(fractions[key] + gain, 10000)
        values[key] += whole
        fractions[key] = fraction if values[key] < caps[key] else 0
        granted[key] = gain
    return SkillGrowth(values, fractions, GrowthBudget(budget.age, used), granted)
