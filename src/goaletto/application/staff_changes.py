"""Confirmed staff dismissal/replacement in the caller's single transaction."""

from datetime import datetime
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface, require_utc
from goaletto.application.candidates import pool_calendar, sign_candidate
from goaletto.application.finances import close_salary
from goaletto.application.game_actions import Actor, PermissionDenied, owned_club, user_effect
from goaletto.application.market import owner_valid
from goaletto.domain.finances import POLICY_VERSION, staff_fee, staff_wage
from goaletto.infrastructure.models import (
    CandidateOffer,
    CandidatePool,
    CandidateSigning,
    Person,
    SalaryContract,
    Staff,
    StaffChange,
    StaffQualification,
)


async def staff_terms(
    session: AsyncSession, person_id: UUID, role: str, *, incoming: bool
) -> dict[str, str | int]:
    person = await session.get(Person, person_id, populate_existing=True)
    staff = await session.get(Staff, person_id, populate_existing=True)
    qualification = await session.get(StaffQualification, (person_id, role), populate_existing=True)
    if (
        person is None
        or staff is None
        or qualification is None
        or person.career != "staff"
        or staff.status not in {"active", "last_season"}
    ):
        raise ValueError("Staff unavailable")
    terms: dict[str, str | int] = {
        "id": str(person.id),
        "name": person.name,
        "age": person.age,
        "skill": qualification.skill,
        "status": staff.status,
    }
    if incoming:
        offer = await session.get(CandidateOffer, person_id, populate_existing=True)
        if (
            staff.club_id is not None
            or offer is None
            or offer.status != "available"
            or offer.kind != "staff"
            or offer.profile != role
        ):
            raise ValueError("Staff offer unavailable")
        terms.update(
            fee=offer.fee,
            wage=offer.weekly_rate,
            policy=offer.economy_policy,
            offer_at=offer.created_at.isoformat(),
        )
    else:
        salary = await session.scalar(
            select(SalaryContract).where(
                SalaryContract.person_id == person_id, SalaryContract.ended_at.is_(None)
            )
        )
        if staff.role != role or salary is None:
            raise ValueError("Employee contract unavailable")
        terms.update(wage=salary.weekly_rate, salary_id=str(salary.id))
    return terms


async def quote_change(
    session: AsyncSession,
    actor: Actor,
    ownership_id: UUID,
    *,
    incoming_id: UUID | None = None,
    outgoing_id: UUID | None = None,
    quoted_fee: int | None = None,
) -> StaffChange:
    owner, club = await owned_club(session, actor)
    if owner.id != ownership_id:
        raise PermissionDenied("Stale owner")
    if incoming_id is not None:
        offer = await session.get(CandidateOffer, incoming_id)
        if offer is None or offer.kind != "staff" or offer.fee != quoted_fee:
            raise ValueError("Stale offer")
        role = offer.profile
        outgoing_id = await session.scalar(
            select(Staff.person_id).where(Staff.club_id == club.id, Staff.role == role)
        )
    else:
        staff = await session.get(Staff, outgoing_id) if outgoing_id else None
        if staff is None or staff.club_id != club.id or staff.role is None:
            raise ValueError("Employee left")
        role = staff.role
    terms = {}
    if outgoing_id:
        terms["outgoing"] = await staff_terms(session, outgoing_id, role, incoming=False)
    if incoming_id:
        terms["incoming"] = await staff_terms(session, incoming_id, role, incoming=True)
    quote_id = uuid5(actor.operation_id, "staff-change:v1")
    row = await session.get(StaffChange, quote_id)
    if row is not None:
        if (
            row.club_id,
            row.ownership_id,
            row.role,
            row.outgoing_id,
            row.incoming_id,
            row.terms,
        ) != (club.id, owner.id, role, outgoing_id, incoming_id, terms):
            raise ValueError("Confirmation operation reused")
        return row
    row = StaffChange(
        id=quote_id,
        club_id=club.id,
        ownership_id=owner.id,
        role=role,
        outgoing_id=outgoing_id,
        incoming_id=incoming_id,
        terms=terms,
        created_at=actor.at,
    )
    session.add(row)
    await user_effect(
        session, actor, "market_opened", Surface.CLUB, ownership=owner, club_id=club.id
    )
    await session.flush()
    return row


async def release_staff(
    session: AsyncSession, person_id: UUID, club_id: UUID, at: datetime, *, normalize: bool = True
) -> None:
    staff = await session.scalar(
        select(Staff)
        .where(Staff.person_id == person_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    person = await session.get(Person, person_id)
    if (
        staff is None
        or person is None
        or staff.club_id != club_id
        or staff.role is None
        or staff.status not in {"active", "last_season"}
    ):
        raise ValueError("Employee changed")
    role = staff.role
    qualification = await session.get(StaffQualification, (person_id, role))
    if qualification is None:
        raise ValueError("Missing qualification")
    await close_salary(session, person_id, at)
    staff.club_id, staff.role = None, None
    season = await pool_calendar(session)
    await session.execute(
        insert(CandidatePool)
        .values(
            kind="staff",
            profile=role,
            target=2,
            generation=0,
            home_league_set_id=season.league_set_id,
        )
        .on_conflict_do_nothing()
    )
    offer = await session.get(CandidateOffer, person_id)
    fee, wage = (
        staff_fee(role, qualification.skill, person.age),
        staff_wage(role, qualification.skill, person.age),
    )
    if offer is None:
        offer = CandidateOffer(
            person_id=person_id,
            kind="staff",
            pool_profile=role,
            profile=role,
            fee=fee,
            weekly_rate=wage,
            economy_policy=POLICY_VERSION,
            status="available",
            created_at=at,
        )
        session.add(offer)
    else:
        offer.kind, offer.pool_profile, offer.profile = "staff", role, role
        offer.fee, offer.weekly_rate, offer.economy_policy = fee, wage, POLICY_VERSION
        offer.status, offer.created_at = "available", at
    await session.flush()
    from goaletto.application.scouting import cancel_invalid

    await cancel_invalid(session, club_id, at)
    if role == "head_coach" and normalize:
        await normalize_tactic(session, club_id)


async def normalize_tactic(session: AsyncSession, club_id: UUID) -> None:
    from goaletto.application.coaches import coach_snapshot
    from goaletto.infrastructure.models import ClubLineup

    lineup = await session.get(ClubLineup, club_id)
    coach = await coach_snapshot(session, club_id)
    if lineup and lineup.tactics.get("tactic", "normal") not in coach.abilities.tactics:
        lineup.tactics = {
            **lineup.tactics,
            "tactic": "normal",
            "pressing": False,
            "counterattack": False,
        }
        lineup.version += 1
        lineup.tactics_version += 1


async def execute_change(
    session: AsyncSession, actor: Actor, quote_id: UUID, *, at: datetime
) -> StaffChange:
    require_utc(at)
    owner, club = await owned_club(session, actor)
    row = await session.scalar(
        select(StaffChange)
        .where(StaffChange.id == quote_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        row is None
        or row.club_id != club.id
        or row.ownership_id != owner.id
        or not await owner_valid(session, owner.id, club.id, at)
    ):
        raise PermissionDenied("Stale staff confirmation")
    if row.executed_at is not None:
        return row
    if await session.get(CandidateSigning, actor.operation_id) is not None:
        raise ValueError("Signing operation belongs to another confirmation")
    if at < max(actor.at, row.created_at) or await session.scalar(
        select(StaffChange.id).where(StaffChange.execution_operation_id == actor.operation_id)
    ):
        raise ValueError("Conflicting staff operation")
    current = await session.scalar(
        select(Staff.person_id).where(Staff.club_id == club.id, Staff.role == row.role)
    )
    if current != row.outgoing_id:
        raise ValueError("Staff employment changed")
    terms = {}
    if row.outgoing_id:
        terms["outgoing"] = await staff_terms(session, row.outgoing_id, row.role, incoming=False)
    if row.incoming_id:
        terms["incoming"] = await staff_terms(session, row.incoming_id, row.role, incoming=True)
    if terms != row.terms:
        raise ValueError("Staff terms changed")
    if row.incoming_id:
        await sign_candidate(
            session,
            actor,
            owner.id,
            row.incoming_id,
            int(terms["incoming"]["fee"]),
            at=at,
            expected_outgoing=row.outgoing_id,
        )
    else:
        assert row.outgoing_id is not None
        await release_staff(session, row.outgoing_id, club.id, at)
        await user_effect(
            session, actor, "club_opened", Surface.CLUB, ownership=owner, club_id=club.id
        )
    row.executed_at, row.execution_operation_id = at, actor.operation_id
    await session.flush()
    return row
