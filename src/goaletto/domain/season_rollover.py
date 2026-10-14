"""Pure offseason timing and comparable bot youth profiles."""

from datetime import datetime, timedelta
from random import Random
from uuid import UUID

from goaletto.domain.calendar import next_season_kickoff
from goaletto.domain.names import international_catalog
from goaletto.domain.world import _skills, stable_seed

ROLLOVER_POLICY = "season-rollover-v1"


def rollover_kickoff(first: datetime, finished: datetime, at: datetime) -> datetime:
    for value in (finished, at):
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("Rollover times must be UTC")
    if finished > at:
        raise ValueError("Season has not finished")

    def saturday_after(value: datetime) -> datetime:
        candidate = (value + timedelta(days=(5 - value.weekday()) % 7)).replace(
            hour=18, minute=0, second=0, microsecond=0
        )
        return candidate if candidate > value else candidate + timedelta(weeks=1)

    return max(
        next_season_kickoff(first),
        saturday_after(finished) + timedelta(weeks=2),
        saturday_after(at + timedelta(minutes=5, seconds=1)),
    )


def youth_profile(seed: int, person_id: UUID, position: str) -> tuple[str, tuple[int, ...]]:
    """Reuse the approved starting-world baseline, without training/AI bonuses."""
    if not isinstance(person_id, UUID):
        raise ValueError("Youth identity must be UUID")
    baseline = {"DM": "MC", "AMC": "FC", "AML": "ML", "AMR": "MR"}.get(position, position)
    if baseline not in {"GK", "DL", "DC", "DR", "ML", "MC", "MR", "FC"}:
        raise ValueError("Youth position lacks an approved baseline")
    local_seed = stable_seed(seed, f"youth:{person_id}")
    catalog = international_catalog(seed)
    _, name = catalog[local_seed % len(catalog)]
    return name, _skills(baseline, Random(local_seed))
