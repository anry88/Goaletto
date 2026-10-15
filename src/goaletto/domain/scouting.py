"""Versioned individual scouting profiles; all draws use a persisted mission seed."""

from random import Random
from uuid import UUID

from goaletto.domain.finances import PRIMARY, SCOUT_SEARCH_DAYS
from goaletto.domain.market_players import MarketPlayer
from goaletto.domain.player_positions import alternative_positions
from goaletto.domain.world import SKILL_NAMES, stable_seed

POLICY = "scouting-v1"


def findings_count(days: int) -> int:
    if type(days) is not int or days not in SCOUT_SEARCH_DAYS:
        raise ValueError("Invalid search duration")
    return days // 4


def discovery(person_id: UUID, position: str, skill: int) -> MarketPlayer:
    if position not in PRIMARY or type(skill) is not int or not 1 <= skill <= 20:
        raise ValueError("Invalid scouting profile")
    rng = Random(stable_seed(0, f"{POLICY}:{person_id}:{position}:{skill}"))
    # Individual ratings across 1..20, with a modest scout shift and rare extremes.
    core = max(1, min(20, 2 + sum(rng.randrange(17) for _ in range(3)) // 3 + (skill - 10) // 5))
    values = [max(1, min(20, core + rng.randint(-5, 2))) for _ in range(8)]
    values[PRIMARY[position]] = core
    values[6], values[7] = (
        max(1, min(20, core + rng.randint(-3, 1))),
        max(1, min(20, core + rng.randint(-3, 1))),
    )
    if position != "GK":
        values[0] = rng.randint(1, 3)
    caps = {
        key: min(20, value + rng.randint(1, 6) + skill // 6)
        for key, value in zip(SKILL_NAMES[:8], values, strict=True)
    }
    return MarketPlayer(
        rng.randint(17, 21),
        (*values, rng.randint(1, 20), rng.randint(1, 20)),
        caps,
        alternative_positions(person_id, position),
    )


def potential_range(cap: int, skill: int) -> tuple[int, int]:
    if not 1 <= cap <= 20 or not 1 <= skill <= 20:
        raise ValueError("Invalid potential report")
    error = (20 - skill + 3) // 4
    return max(1, cap - error), min(20, cap + error)
