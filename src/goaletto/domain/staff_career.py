"""Persist this deterministic career once at retirement, independently of supply."""

from uuid import UUID, uuid5

from goaletto.domain.candidates import STAFF_ROLES
from goaletto.domain.staff_quality import generated_staff
from goaletto.domain.world import stable_seed

POLICY = "retired-staff-v1"
RESPONSE_DAYS = 8


def retirement_career(person_id: UUID, season_id: UUID) -> tuple[str, int]:
    key = uuid5(person_id, f"{POLICY}:{season_id}")
    role = STAFF_ROLES[stable_seed(0, str(key)) % len(STAFF_ROLES)]
    _, skill = generated_staff(key, role)
    return role, skill


def loyal_wage(normal_wage: int) -> int:
    if type(normal_wage) is not int or normal_wage <= 0:
        raise ValueError("Invalid ordinary wage")
    return max(1, normal_wage * 4 // 5)
