"""Persist a shared growth budget; a transfer or new owner never resets it."""

from goaletto.domain.development import GROWTH_POLICY, GrowthBudget
from goaletto.domain.position_experience import (
    PERCENT_POLICY,
    validate_experience,
    validate_percent_experience,
)
from goaletto.infrastructure.models import Player, PlayerDevelopment


def position_experience(player: Player, development: PlayerDevelopment) -> dict[str, int]:
    if player.position_experience_policy == PERCENT_POLICY:
        return validate_percent_experience(player.position_experience or {})
    if player.position_experience:
        return validate_experience(player.position_experience)
    # Preserve a fresh/legacy person's prior general points and fractions once.
    total = player.experience * 10000 + development.progress["experience"]
    return {player.position: total} if total else {}


def growth_budget(development: PlayerDevelopment, age: int) -> GrowthBudget:
    if development.growth_policy not in (None, GROWTH_POLICY):
        raise ValueError("Unknown development budget policy")
    previous_age = development.growth_age or 0
    if previous_age > age:
        raise ValueError("Development age cannot move backwards")
    if previous_age != age:
        return GrowthBudget(age)
    return GrowthBudget(age, dict(development.season_gains))


def save_budget(development: PlayerDevelopment, budget: GrowthBudget) -> None:
    development.growth_policy = GROWTH_POLICY
    development.growth_age, development.season_gains = budget.age, dict(budget.used)
