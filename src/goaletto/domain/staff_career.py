"""Persist this deterministic career once at retirement, independently of supply."""

from uuid import UUID, uuid5

from goaletto.domain.candidates import STAFF_ROLES
from goaletto.domain.staff_quality import generated_staff
from goaletto.domain.world import stable_seed

LEGACY_POLICY = "retired-staff-v1"
POLICY = "retired-staff-v2"
RESPONSE_DAYS = 8


def retirement_career(person_id: UUID, season_id: UUID, *, policy: str = POLICY) -> tuple[str, int]:
    if policy not in (LEGACY_POLICY, POLICY):
        raise ValueError("Unknown retirement career policy")
    roles = STAFF_ROLES if policy == POLICY else STAFF_ROLES[:7]
    key = uuid5(person_id, f"{policy}:{season_id}")
    role = roles[stable_seed(0, str(key)) % len(roles)]
    _, skill = generated_staff(key, role)
    return role, skill


def loyal_wage(normal_wage: int) -> int:
    if type(normal_wage) is not int or normal_wage <= 0:
        raise ValueError("Invalid ordinary wage")
    return max(1, normal_wage * 4 // 5)
