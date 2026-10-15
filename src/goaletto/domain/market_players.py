"""Seeded public-market adults, separate from scout discoveries and bot youth."""

from dataclasses import dataclass
from random import Random
from uuid import UUID

from goaletto.domain.finances import PRIMARY
from goaletto.domain.world import SKILL_NAMES, stable_seed

PLAYER_MARKET_POLICY = "market-players-v2"
QUALITY_WEIGHTS = (15, 35, 32, 15, 3)


@dataclass(frozen=True)
class MarketPlayer:
    age: int
    skills: tuple[int, ...]
    caps: dict[str, int]


def market_player(person_id: UUID, position: str) -> MarketPlayer:
    if not isinstance(person_id, UUID) or position not in PRIMARY:
        raise ValueError("Invalid market player")
    rng = Random(stable_seed(0, f"{PLAYER_MARKET_POLICY}:{person_id}:{position}"))
    tier = rng.choices(range(1, 6), weights=QUALITY_WEIGHTS, k=1)[0]
    low, high = (tier - 1) * 4 + 1, tier * 4
    age = rng.randint(26, 30)
    core = rng.randint(low, high)
    primary = PRIMARY[position]
    values = [max(1, min(20, core + rng.randint(-4, 1))) for _ in range(8)]
    values[primary] = core
    values[6], values[7] = rng.randint(low, high), rng.randint(low, high)
    if position != "GK":
        values[0] = rng.randint(1, min(3, high))
    caps = {
        key: min(20, value + rng.randint(0, 4))
        for key, value in zip(SKILL_NAMES[:8], values, strict=True)
    }
    return MarketPlayer(age, (*values, rng.randint(1, 20), rng.randint(1, 20)), caps)
