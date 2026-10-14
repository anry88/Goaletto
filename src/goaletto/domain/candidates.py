"""Bounded global candidate supply and reproducible baseline profiles."""

from uuid import UUID

from goaletto.domain.finances import STAFF_WAGES, player_wage, staff_wage
from goaletto.domain.names import international_catalog
from goaletto.domain.season_rollover import youth_profile
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
        baseline = {"DM": "MC", "AMC": "FC", "AML": "ML", "AMR": "MR"}.get(profile, profile)
        name, skills = youth_profile(seed, person_id, baseline)
        # Same minimum as auction-v1; signing is an economy sink, not a club sale.
        return name, 18, skills, 100_000, player_wage(skills[:8])
    if kind == "staff" and profile in STAFF_ROLES:
        catalog = international_catalog(seed)
        _, name = catalog[seed % len(catalog)]
        wage = staff_wage(profile)
        return name, 40, (6 + seed % 5,), 4 * wage, wage
    raise ValueError("Unsupported candidate profile")
