"""Current staff access and the exact employment bound to a chosen session."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.domain.staff_effects import POLICY
from goaletto.domain.training import SPECIALISTS, Focus
from goaletto.infrastructure.models import SalaryContract, Staff, StaffQualification


async def staff_roster(
    session: AsyncSession, club_id: UUID, at: datetime
) -> dict[str, dict[str, Any]]:
    rows = (
        await session.execute(
            select(Staff.role, Staff.person_id, StaffQualification.skill, SalaryContract.id)
            .select_from(Staff)
            .join(
                StaffQualification,
                (StaffQualification.person_id == Staff.person_id)
                & (StaffQualification.role == Staff.role),
            )
            .join(
                SalaryContract,
                (SalaryContract.person_id == Staff.person_id)
                & (SalaryContract.club_id == Staff.club_id)
                & (SalaryContract.role == Staff.role),
            )
            .where(
                Staff.club_id == club_id,
                Staff.status.in_(("active", "last_season")),
                SalaryContract.started_at <= at,
                SalaryContract.ended_at.is_(None),
            )
        )
    ).all()
    return {
        role: {"person_id": str(person_id), "skill": skill, "contract_id": str(contract_id)}
        for role, person_id, skill, contract_id in rows
    }


def training_staff(roster: dict[str, dict[str, Any]], focus: Focus) -> dict[str, Any]:
    roles = {"head_coach", "physiotherapist"}
    if focus != Focus.TEAM:
        roles.add(SPECIALISTS[focus])
    return {"policy": POLICY, "roles": {role: roster.get(role) for role in sorted(roles)}}


def qualifications(roster: dict[str, dict[str, Any]]) -> dict[str, int]:
    return {role: int(row["skill"]) for role, row in roster.items()}
