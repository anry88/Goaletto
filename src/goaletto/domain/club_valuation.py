"""Gross club assets, valuation-v1. Cash is excluded to avoid circular credit."""

from goaletto.domain.academy import upgrade_cost
from goaletto.domain.finances import STADIUM_CAPACITIES, STADIUM_PRICES, money

POLICY = "club-assets-v1"


def facilities(capacity: int, academy_level: int) -> tuple[int, int]:
    if capacity not in STADIUM_CAPACITIES or not 1 <= academy_level <= 5:
        raise ValueError("Invalid club facilities")
    # Replacement values of the starting facilities plus every construction step.
    stadium = 300_000 + sum(STADIUM_PRICES[1 : STADIUM_CAPACITIES.index(capacity) + 1])
    academy = 100_000 + sum(upgrade_cost(level) for level in range(1, academy_level))
    return money(stadium), money(academy)


def bankrupt(balance: int, assets: int) -> bool:
    if type(balance) is not int or type(assets) is not int or assets < 0:
        raise ValueError("Invalid credit balance")
    return balance < -assets
