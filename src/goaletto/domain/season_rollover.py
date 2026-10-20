"""Pure offseason timing and comparable bot youth profiles."""

from datetime import datetime
from random import Random
from uuid import UUID

from goaletto.domain.person_names import person_name
from goaletto.domain.world import _skills, stable_seed

ROLLOVER_POLICY = "season-rollover-v1"


def rollover_kickoff(first: datetime, finished: datetime, at: datetime) -> datetime:
    from goaletto.domain.game_cycle import next_first_league

    return next_first_league(first, finished, at)


def youth_profile(seed: int, person_id: UUID, position: str) -> tuple[str, tuple[int, ...]]:
    """Reuse the approved starting-world baseline, without training/AI bonuses."""
    if not isinstance(person_id, UUID):
        raise ValueError("Youth identity must be UUID")
    baseline = {"DM": "MC", "AMC": "FC", "AML": "ML", "AMR": "MR"}.get(position, position)
    if baseline not in {"GK", "DL", "DC", "DR", "ML", "MC", "MR", "FC"}:
        raise ValueError("Youth position lacks an approved baseline")
    local_seed = stable_seed(seed, f"youth:{person_id}")
    name = person_name(person_id)
    return name, _skills(baseline, Random(local_seed))
