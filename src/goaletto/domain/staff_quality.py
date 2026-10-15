"""Versioned, role-specific staff qualifications; no football OVR or I/O."""

from dataclasses import dataclass
from random import Random
from uuid import UUID

from goaletto.domain.finances import STAFF_WAGES
from goaletto.domain.match_types import Tactic
from goaletto.domain.world import stable_seed

STAFF_QUALITY_POLICY = "staff-quality-v1"
# Calibratable generation weights, not a claim of user-approved probabilities.
QUALITY_WEIGHTS = (40, 32, 20, 7, 1)
QUALITY_NAMES = ("Начинающий", "Базовый", "Опытный", "Сильный", "Элитный")


def quality_tier(skill: int) -> int:
    if type(skill) is not int or not 1 <= skill <= 20:
        raise ValueError("Staff skill must be 1..20")
    return (skill - 1) // 4 + 1


@dataclass(frozen=True)
class StaffAbilities:
    policy_version: str
    role: str
    tier: int
    skill: int
    training: tuple[str, ...] = ()
    recovery: int = 0
    morale: int = 0
    leadership: int = 0
    tactics: tuple[Tactic, ...] = ()
    tactic_changes: int = 0
    instruction_changes: int = 0
    mentality_changes: int = 0
    scouting: int = 0


def staff_abilities(role: str, skill: int, version: str = STAFF_QUALITY_POLICY) -> StaffAbilities:
    """Capability contract for consumers AND-109/111/112/116, not stacked effects.

    Strengths are bounded 1..20 qualification ratings, not percentage bonuses.
    Match/training consumers must explicitly opt into this version in their snapshots.
    """
    if version != STAFF_QUALITY_POLICY or role not in STAFF_WAGES:
        raise ValueError("Unknown staff qualification policy or role")
    tier = quality_tier(skill)
    training = {
        "head_coach": ("team",),
        "goalkeeping_coach": ("goalkeeping",),
        "defence_coach": ("defending",),
        "attack_coach": ("attacking",),
        "fitness_coach": ("physical",),
    }.get(role, ())
    tactics = (
        Tactic.NORMAL,
        Tactic.DEFENCE,
        Tactic.COUNTER,
        Tactic.WINGS,
        Tactic.LONG_BALLS,
        Tactic.PRESSING,
    )
    return StaffAbilities(
        version,
        role,
        tier,
        skill,
        training=training,
        recovery=skill if role in {"physiotherapist", "fitness_coach"} else 0,
        morale=skill if role == "head_coach" else 0,
        leadership=skill if role == "head_coach" else 0,
        tactics=tactics[: (1, 2, 3, 5, 6)[tier - 1]] if role == "head_coach" else (),
        tactic_changes=(0, 1, 2, 3, 5)[tier - 1] if role == "head_coach" else 0,
        instruction_changes=(1, 2, 3, 5, 8)[tier - 1] if role == "head_coach" else 0,
        mentality_changes=(1, 2, 3, 4, 6)[tier - 1] if role == "head_coach" else 0,
        scouting=skill if role == "scout" else 0,
    )


def generated_staff(person_id: UUID, role: str) -> tuple[int, int]:
    if role not in STAFF_WAGES:
        raise ValueError("Unknown staff role")
    rng = Random(stable_seed(0, f"{STAFF_QUALITY_POLICY}:{person_id}:{role}"))
    draw = rng.randrange(sum(QUALITY_WEIGHTS))
    tier = 1
    for weight in QUALITY_WEIGHTS:
        if draw < weight:
            break
        draw -= weight
        tier += 1
    return 50 + rng.randrange(7), (tier - 1) * 4 + rng.randint(1, 4)
