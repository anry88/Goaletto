"""Seeded adjacent positions, independent of current club and immutable snapshots."""

from random import Random
from uuid import UUID

from goaletto.domain.world import stable_seed

POLICY = "player-positions-v1"
ADJACENT = {
    "GK": (),
    "DC": ("DL", "DR", "DM"),
    "DL": ("DC", "ML"),
    "DR": ("DC", "MR"),
    "DM": ("DC", "MC"),
    "MC": ("DM", "ML", "MR", "AMC"),
    "ML": ("DL", "MC", "AML"),
    "MR": ("DR", "MC", "AMR"),
    "AMC": ("MC", "FC", "AML", "AMR"),
    "AML": ("ML", "AMC", "FC"),
    "AMR": ("MR", "AMC", "FC"),
    "FC": ("AMC", "AML", "AMR"),
}


def alternative_positions(person_id: UUID, primary: str) -> tuple[str, ...]:
    if primary not in ADJACENT:
        raise ValueError("Unknown position")
    rng = Random(stable_seed(0, f"{POLICY}:{person_id}:{primary}"))
    draw = rng.randrange(100)
    count = 0 if draw < 60 else 1 if draw < 90 else 2
    return tuple(rng.sample(ADJACENT[primary], min(count, len(ADJACENT[primary]))))
