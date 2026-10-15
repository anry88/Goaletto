"""Authorized staff details, preserving employment terms and the person's identity."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface
from goaletto.application.game_actions import Actor, PermissionDenied, owned_club, user_effect
from goaletto.application.staff_changes import staff_terms
from goaletto.infrastructure.models import (
    AppearanceIdentity,
    CandidateOffer,
    Club,
    Person,
    Player,
    Staff,
    StaffCareer,
)


@dataclass(frozen=True)
class StaffCard:
    ownership_id: UUID
    person: Person
    role: str
    terms: dict[str, str | int]
    employed: bool
    replacing: bool
    career: StaffCareer | None
    former_club: str | None
    former_position: str | None
    catalog_version: int
    kit_primary: str
    kit_secondary: str
    kit_version: int


async def load_staff_card(
    session: AsyncSession, actor: Actor, ownership_id: UUID, person_id: UUID
) -> StaffCard:
    owner, club = await owned_club(session, actor)
    if owner.id != ownership_id:
        raise PermissionDenied("Stale staff card")
    person = await session.get(Person, person_id)
    worker = await session.get(Staff, person_id)
    if person is None or worker is None or person.career != "staff":
        raise ValueError("Unknown staff")
    employed = worker.club_id == club.id
    if worker.club_id is not None and not employed:
        raise PermissionDenied("Foreign employee")
    offer = await session.get(CandidateOffer, person_id) if not employed else None
    career = await session.get(StaffCareer, person_id)
    if not employed and (
        offer is None
        or offer.kind != "staff"
        or offer.status not in {"available", "private"}
        or (
            offer.status == "private"
            and (
                career is None
                or career.origin_club_id != club.id
                or career.status != "pending"
                or career.expires_at <= actor.at
            )
        )
    ):
        raise ValueError("Staff offer unavailable")
    role = worker.role if employed else offer.profile if offer else None
    if role is None:
        raise ValueError("Missing staff role")
    terms = await staff_terms(
        session, person_id, role, incoming=not employed, club_id=club.id, at=actor.at
    )
    from sqlalchemy import select

    replacing = not employed and bool(
        await session.scalar(
            select(Staff.person_id).where(Staff.club_id == club.id, Staff.role == role)
        )
    )
    identity = await session.get(AppearanceIdentity, person.appearance_identity_id)
    if identity is None:
        raise ValueError("Staff face is missing")
    former = (
        await session.get(Club, career.origin_club_id) if career and career.origin_club_id else None
    )
    footballer = await session.get(Player, person.id) if career else None
    await user_effect(
        session, actor, "market_opened", Surface.CLUB, ownership=owner, club_id=club.id
    )
    return StaffCard(
        owner.id,
        person,
        role,
        terms,
        employed,
        replacing,
        career,
        former.name if former else None,
        footballer.position if footballer else None,
        identity.catalog_version,
        club.kit_primary,
        club.kit_secondary,
        club.kit_version,
    )
