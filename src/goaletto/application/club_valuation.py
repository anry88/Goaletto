"""Current owned assets; no future sponsor income or speculative auction prices."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.domain.club_valuation import facilities
from goaletto.domain.finances import player_fee, staff_fee
from goaletto.domain.world import SKILL_NAMES
from goaletto.infrastructure.models import (
    Club,
    Person,
    Player,
    StadiumExpansion,
    Staff,
    StaffQualification,
)


async def valuation(session: AsyncSession, club: Club) -> dict[str, int]:
    rows = (
        await session.execute(
            select(Player, Person)
            .join(Person, Person.id == Player.person_id)
            .where(Player.club_id == club.id, Player.status != "retired")
        )
    ).all()
    # An announced last season does not remove an employed person from club assets.
    players = sum(
        player_fee(
            tuple(getattr(p, key) for key in SKILL_NAMES[:8]),
            person.age,
            p.position,
            1 + len(p.alternative_positions),
        )
        for p, person in rows
    )
    workers = (
        await session.execute(
            select(Staff, Person, StaffQualification)
            .join(Person, Person.id == Staff.person_id)
            .join(
                StaffQualification,
                (StaffQualification.person_id == Staff.person_id)
                & (StaffQualification.role == Staff.role),
            )
            .where(Staff.club_id == club.id, Staff.status.in_(("active", "last_season")))
        )
    ).all()
    staff = sum(staff_fee(w.role, q.skill, person.age) for w, person, q in workers)
    stadium, academy = facilities(club.stadium_capacity, club.academy_level)
    construction = int(
        await session.scalar(
            select(func.coalesce(func.sum(StadiumExpansion.cost), 0)).where(
                StadiumExpansion.club_id == club.id, StadiumExpansion.status == "queued"
            )
        )
        or 0
    )
    return {
        "players": players,
        "staff": staff,
        "stadium": stadium,
        "academy": academy,
        "construction": construction,
    }
