"""Bounded global candidate supply and reproducible baseline profiles."""

from uuid import UUID

from goaletto.domain.finances import STAFF_WAGES, player_fee, player_wage, staff_fee, staff_wage
from goaletto.domain.market_players import market_player
from goaletto.domain.names import international_catalog
from goaletto.domain.staff_quality import generated_staff
from goaletto.domain.world import stable_seed

POOL_POLICY = "candidate-pool-v1"
PLAYER_POSITIONS = ("GK", "DL", "DC", "DR", "DM", "ML", "MC", "MR", "AMC", "AML", "AMR", "FC")
STAFF_ROLES = tuple(STAFF_WAGES)


def deficit(available: int, target: int, limit: int | None = None) -> int:
    if any(type(n) is not int or n < 0 for n in (available, target)):
        raise ValueError("Supply counts must be nonnegative integers")
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError("Invalid replenishment limit")
    missing = max(0, target - available)
    return missing if limit is None else min(missing, limit)


def candidate_profile(
    person_id: UUID, kind: str, profile: str
) -> tuple[str, int, tuple[int, ...], int, int]:
    seed = stable_seed(0, f"{POOL_POLICY}:{person_id}")
    if kind == "player" and profile in PLAYER_POSITIONS:
        catalog = international_catalog(seed)
        _, name = catalog[seed % len(catalog)]
        player = market_player(person_id, profile)
        return (
            name,
            player.age,
            player.skills,
            player_fee(player.skills[:8], player.age, profile, 1 + len(player.alternatives)),
            player_wage(player.skills[:8], player.age, profile, 1 + len(player.alternatives)),
        )
    if kind == "staff" and profile in STAFF_ROLES:
        catalog = international_catalog(seed)
        _, name = catalog[seed % len(catalog)]
        age, skill = generated_staff(person_id, profile)
        wage = staff_wage(profile, skill, age)
        return name, age, (skill,), staff_fee(profile, skill, age), wage
    raise ValueError("Unsupported candidate profile")
