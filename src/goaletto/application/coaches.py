"""Pinned match capabilities and baseline coaches for seeding and new ownership."""

from datetime import datetime
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.game_actions import allocation_lock
from goaletto.application.persons import NewPerson, create_person
from goaletto.domain.candidates import candidate_profile
from goaletto.domain.match_types import CoachSnapshot
from goaletto.infrastructure.models import Club, League, Person, Season, Staff, StaffQualification
from goaletto.portraits.whole import load_whole_catalog


async def coach_snapshot(session: AsyncSession, club_id: UUID) -> CoachSnapshot:
    row = (
        await session.execute(
            select(Staff.person_id, StaffQualification.skill)
            .join(
                StaffQualification,
                (StaffQualification.person_id == Staff.person_id)
                & (StaffQualification.role == Staff.role),
            )
            .where(
                Staff.club_id == club_id,
                Staff.role == "head_coach",
                Staff.status.in_(("active", "last_season")),
            )
        )
    ).one_or_none()
    return CoachSnapshot(row[0], row[1]) if row else CoachSnapshot(None)


async def ensure_starting_coaches(
    session: AsyncSession, at: datetime, *, league_set_id: UUID | None = None
) -> int:
    """Init/backfill only. A fired/retired baseline coach is never created again."""
    await allocation_lock(session)
    query = select(Club, League.league_set_id).join(League).order_by(Club.id)
    if league_set_id is not None:
        query = query.where(League.league_set_id == league_set_id)
    eligible = _starting_coach_variants()
    count = 0
    for club, set_id in (await session.execute(query)).all():
        person_id = uuid5(club.id, "starting-head-coach:v1")
        if (
            await session.get(Person, person_id)
            or (await coach_snapshot(session, club.id)).person_id
        ):
            continue
        season = await session.scalar(
            select(Season)
            .where(Season.league_set_id == set_id)
            .order_by(Season.number.desc())
            .limit(1)
        )
        if season is None:
            raise ValueError("Missing coach calendar")
        await install_starting_coach(
            session,
            club.id,
            season,
            person_id,
            f"starting-head-coach:v1:{club.id}",
            at,
            eligible=eligible,
        )
        count += 1
    return count


def _starting_coach_variants() -> tuple[str, ...]:
    catalog = load_whole_catalog()
    return tuple(
        key
        for key, entry in catalog["variants"].items()
        if entry["bank"] == "staff" and entry.get("stage") == "staff-middle"
    )


async def install_starting_coach(
    session: AsyncSession,
    club_id: UUID,
    season: Season,
    person_id: UUID,
    creation_key: str,
    at: datetime,
    *,
    eligible: tuple[str, ...] | None = None,
) -> None:
    from goaletto.application.candidates import lifecycle_event
    from goaletto.application.finances import salary_contract

    name, age, _, _, _ = candidate_profile(person_id, "staff", "head_coach")
    person = await create_person(
        session,
        NewPerson(
            id=person_id,
            creation_key=creation_key,
            name=name,
            age=age,
            career="staff",
            origin_bank="staff",
            eligible_variant_ids=eligible if eligible is not None else _starting_coach_variants(),
            home_league_set_id=season.league_set_id,
        ),
    )
    person.age_applied_for_season_id = season.id
    session.add(Staff(person_id=person_id, club_id=club_id, role="head_coach", status="active"))
    await session.flush()
    session.add(
        StaffQualification(
            person_id=person_id, role="head_coach", skill=1, qualified_for_season_id=season.id
        )
    )
    session.add(lifecycle_event(person, season, "age_applied", at, staff=True))
    session.add(lifecycle_event(person, season, "staff_last_decision", at, staff=True))
    await session.flush()
    await salary_contract(session, person_id, club_id, "head_coach", at)
