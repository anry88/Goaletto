"""Reproducible comparison of free starters, academy level 1 and scout skill 1."""

import json
from random import Random
from statistics import median
from uuid import UUID

from goaletto.domain.academy import candidate
from goaletto.domain.finances import PRIMARY
from goaletto.domain.scouting import discovery
from goaletto.domain.starter_squads import LEGACY_STARTER_POLICY, STARTER_POLICY, starter_skills

POSITIONS = ("GK", "DL", "DC", "DR", "ML", "MC", "MR", "FC")


def quality(values: tuple[int, ...], position: str) -> float:
    return (3 * values[PRIMARY[position]] + values[6] + values[7]) / 5


def distribution(values: list[float]) -> dict[str, float | int]:
    values.sort()
    return {
        "n": len(values),
        "p10": values[len(values) // 10],
        "median": median(values),
        "p90": values[len(values) * 9 // 10],
    }


def main() -> None:
    result = {
        policy: distribution(
            [
                quality(starter_skills(p, Random(i), policy), p)
                for i in range(1000)
                for p in POSITIONS
            ]
        )
        for policy in (LEGACY_STARTER_POLICY, STARTER_POLICY)
    }
    academy = [
        candidate(UUID(int=i), UUID(int=9999), s, 1) for i in range(1, 1001) for s in (1, 2, 3)
    ]
    result["academy-level-1"] = distribution([quality(p.skills, p.position) for p in academy])
    result["scout-skill-1"] = distribution(
        [quality(discovery(UUID(int=i), p, 1).skills, p) for i in range(1, 1001) for p in POSITIONS]
    )
    print(json.dumps({"quality": "(3*primary+stamina+pace)/5", "profiles": result}, indent=2))


if __name__ == "__main__":
    main()
