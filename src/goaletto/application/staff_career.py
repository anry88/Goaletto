"""A retired player's first offer, public release and permanent home-club loyalty."""

from datetime import datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface, require_utc
from goaletto.application.game_actions import (
    Actor,
    PermissionDenied,
    allocation_lock,
    owned_club,
    user_effect,
)
from goaletto.domain.finances import POLICY_VERSION, staff_fee, staff_wage
from goaletto.domain.staff_career import POLICY, RESPONSE_DAYS, loyal_wage, retirement_career
from goaletto.infrastructure.models import (
    CandidateOffer,
    CandidatePool,
    Job,
    Manager,
    OutboxMessage,
    OwnershipPeriod,
    Person,
    Player,
    ScoutDiscovery,
    Staff,
    StaffCareer,
    StaffQualification,
)


async def assign_career(
    session: AsyncSession,
    person: Person,
    season_id: UUID,
    origin_club_id: UUID | None,
    at: datetime,
) -> StaffCareer:
    require_utc(at)
    await allocation_lock(session)
    prior = await session.get(StaffCareer, person.id)
    if prior is not None:
        return prior
    player = await session.get(Player, person.id)
    if (
        player is None
        or player.status != "retired"
        or person.career != "retired"
        or (await session.get(Staff, person.id)) is not None
    ):
        raise ValueError("Only newly retired footballers receive a career")
    if await session.scalar(
        select(ScoutDiscovery.person_id).where(
            ScoutDiscovery.person_id == person.id, ScoutDiscovery.signed_at.is_(None)
        )
    ):
        raise ValueError("Unsigned scout discoveries never enter staff supply")
    role, skill = retirement_career(person.id, season_id)
    owner = (
        await session.scalar(
            select(OwnershipPeriod).where(
                OwnershipPeriod.club_id == origin_club_id,
                OwnershipPeriod.ended_at.is_(None),
                OwnershipPeriod.expires_at > at,
            )
        )
        if origin_club_id
        else None
    )
    person.career = "staff"
    session.add(Staff(person_id=person.id, status="active"))
    await session.flush()
    session.add(
        StaffQualification(
            person_id=person.id, role=role, skill=skill, qualified_for_season_id=season_id
        )
    )
    career = StaffCareer(
        person_id=person.id,
        origin_club_id=origin_club_id,
        season_id=season_id,
        role=role,
        skill=skill,
        policy_version=POLICY,
        created_at=at,
        expires_at=at + timedelta(days=RESPONSE_DAYS),
        status="pending" if owner else "public",
        resolved_at=None if owner else at,
    )
    session.add(career)
    from goaletto.application.candidates import pool_calendar

    calendar = await pool_calendar(session)
    person.home_league_set_id = calendar.league_set_id
    person.age_applied_for_season_id = calendar.id
    from goaletto.application.candidates import lifecycle_event
    from goaletto.infrastructure.models import PersonSeasonEvent

    for stage in ("age_applied", "staff_last_decision", "career_assigned"):
        if (
            await session.scalar(
                select(PersonSeasonEvent.id).where(
                    PersonSeasonEvent.person_id == person.id,
                    PersonSeasonEvent.season_id == calendar.id,
                    PersonSeasonEvent.stage == stage,
                )
            )
            is None
        ):
            session.add(lifecycle_event(person, calendar, stage, at, staff=True))
    await session.execute(
        insert(CandidatePool)
        .values(
            kind="staff",
            profile=role,
            target=2,
            generation=0,
            home_league_set_id=calendar.league_set_id,
        )
        .on_conflict_do_nothing()
    )
    offer = await session.get(CandidateOffer, person.id)
    if offer is not None and offer.kind != "player":
        raise ValueError("Previous player offer must be closed at retirement")
    if offer is None:
        offer = CandidateOffer(person_id=person.id)
        session.add(offer)
    offer.kind, offer.pool_profile, offer.profile = "staff", role, role
    offer.fee, offer.weekly_rate = (
        staff_fee(role, skill, person.age),
        staff_wage(role, skill, person.age),
    )
    offer.economy_policy, offer.created_at = POLICY_VERSION, at
    offer.status = "private" if owner else "available"
    await session.flush()
    if owner:
        session.add(
            Job(
                id=uuid5(person.id, "staff-career:expiry"),
                dedupe_key=f"staff-career:expiry:{person.id}",
                kind="staff.career_expire",
                payload={"version": 1, "person_id": str(person.id)},
                due_at=career.expires_at,
                status="pending",
                attempts=0,
            )
        )
        manager = await session.get(Manager, owner.manager_id)
        if manager is None:
            raise ValueError("Missing former-club owner")
        key = f"staff-career:{person.id}"
        session.add(
            OutboxMessage(
                id=uuid5(person.id, key),
                dedupe_key=key,
                chat_id=manager.telegram_id,
                due_at=at,
                status="pending",
                attempts=0,
                priority=80,
                payload={
                    "version": 1,
                    "kind": "staff_career",
                    "ownership_id": str(owner.id),
                    "person_id": str(person.id),
                    "club_id": str(origin_club_id),
                    "player_name": person.name,
                },
            )
        )
    return career


async def quoted_wage(
    session: AsyncSession, offer: CandidateOffer, club_id: UUID, at: datetime
) -> tuple[int, bool]:
    career = await session.get(StaffCareer, offer.person_id, populate_existing=True)
    if offer.status == "private" and (
        career is None
        or career.status != "pending"
        or career.origin_club_id != club_id
        or at >= career.expires_at
    ):
        raise ValueError("Private career offer unavailable")
    loyal = bool(career and career.origin_club_id == club_id)
    return (loyal_wage(offer.weekly_rate) if loyal else offer.weekly_rate), loyal


async def release_offer(session: AsyncSession, career: StaffCareer, at: datetime) -> None:
    if career.status != "pending":
        return
    require_utc(at)
    if at < career.created_at:
        raise ValueError("Career decision predates retirement")
    offer = await session.get(CandidateOffer, career.person_id)
    if offer is None or offer.status != "private":
        raise ValueError("Career offer state disagrees")
    career.status, career.resolved_at = "public", at
    offer.status = "available"


async def expire_offers(session: AsyncSession, at: datetime) -> None:
    require_utc(at)
    await allocation_lock(session)
    active_owner = (
        select(OwnershipPeriod.id)
        .where(
            OwnershipPeriod.club_id == StaffCareer.origin_club_id,
            OwnershipPeriod.ended_at.is_(None),
            OwnershipPeriod.expires_at > at,
        )
        .exists()
    )
    rows = (
        await session.scalars(
            select(StaffCareer)
            .where(StaffCareer.status == "pending", (StaffCareer.expires_at <= at) | ~active_owner)
            .with_for_update()
        )
    ).all()
    for career in rows:
        await release_offer(session, career, at)
    await session.flush()


async def decline_offer(
    session: AsyncSession, actor: Actor, person_id: UUID, owner_id: UUID
) -> None:
    owner, club = await owned_club(session, actor)
    career = await session.get(StaffCareer, person_id, populate_existing=True)
    if owner.id != owner_id or career is None or career.origin_club_id != club.id:
        raise PermissionDenied("Career offer belongs to another club")
    if career.status == "accepted":
        raise ValueError("Career offer already accepted")
    await release_offer(session, career, actor.at)
    await user_effect(
        session, actor, "market_opened", Surface.CLUB, ownership=owner, club_id=club.id
    )


async def expire_job(session: AsyncSession, job: Job, at: datetime) -> None:
    if (
        set(job.payload) != {"version", "person_id"}
        or type(job.payload["version"]) is not int
        or job.payload["version"] != 1
    ):
        raise ValueError("Invalid career expiry job")
    from goaletto.application.match_runtime import NotReady

    if not await allocation_lock(session, try_lock=True):
        raise NotReady("Busy career expiry")
    career = await session.get(StaffCareer, UUID(job.payload["person_id"]))
    if career is None or job.due_at != career.expires_at:
        raise ValueError("Missing career expiry")
    if at < career.expires_at:
        raise NotReady("Career offer has not expired")
    await release_offer(session, career, at)
