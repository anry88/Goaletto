"""Approved footballer policy. Draw is explicit and persisted by the caller."""

from dataclasses import dataclass

PLAYER_RETIREMENT_POLICY = "player-retirement-v1"
PROBABILITIES_BPS = (500, 1000, 2000, 3500, 5500, 8000, 10000)


@dataclass(frozen=True)
class RetirementDecision:
    age: int
    probability_bps: int
    draw: int | None
    is_last_season: bool
    policy_version: str = PLAYER_RETIREMENT_POLICY


def player_last_season(age: int, draw: int) -> RetirementDecision:
    if type(age) is not int or not 17 <= age <= 38:
        raise ValueError("Active footballer age must be 17 through 38")
    if type(draw) is not int or not 0 <= draw <= 9999:
        raise ValueError("Draw must be an integer from 0 through 9999")
    probability = PROBABILITIES_BPS[age - 32] if age >= 32 else 0
    stored_draw = draw if 0 < probability < 10000 else None
    return RetirementDecision(age, probability, stored_draw, draw < probability)
