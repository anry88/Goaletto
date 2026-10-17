"""Tier-five club entry and atomic world expansion. No Telegram or network calls."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.acquisition import accept_start, telegram_operation
from goaletto.application.analytics import Surface, require_utc
from goaletto.application.game_actions import (
    DEFAULT_LEASE_POLICY,
    Actor,
    LeasePolicy,
    PermissionDenied,
    actor_for,
    allocation_lock,
    end_expired,
    owned_club,
    user_effect,
)
from goaletto.application.world_capacity import (
    LOW_WATER,
    available_clubs,
    ready_set_predicate,
    request_reserve,
)
from goaletto.domain.ownership import ownership_deadline
from goaletto.infrastructure.analytics import start_coverage
from goaletto.infrastructure.models import Club, GameActionReceipt, League, Manager, OwnershipPeriod


@dataclass(frozen=True)
class ClubOffer:
    id: UUID
    name: str
    budget: int
    stadium_capacity: int
    supporters: int


@dataclass(frozen=True)
class Entry:
    actor: Actor
    ownership_id: UUID | None
    club_id: UUID | None
    offers: tuple[ClubOffer, ...]
    no_capacity: bool = False


class ClubUnavailable(ValueError):
    pass


def next_initial_kickoff(at: datetime) -> datetime:
    require_utc(at)
    # A new set begins with a full cycle; the first league date is day four.
    return (at + timedelta(days=4)).replace(hour=18, minute=0, second=0, microsecond=0)


async def offers(session: AsyncSession, page: int = 1) -> tuple[ClubOffer, ...]:
    if type(page) is not int or not 1 <= page <= 10000:
        raise ValueError("Invalid offer page")
    active = (
        select(OwnershipPeriod.id)
        .where(OwnershipPeriod.club_id == Club.id, OwnershipPeriod.ended_at.is_(None))
        .exists()
    )
    rows = (
        await session.scalars(
            select(Club)
            .join(League, League.id == Club.league_id)
            .where(
                League.tier == 5,
                Club.owner_telegram_id.is_(None),
                ~active,
                ready_set_predicate(),
            )
            .order_by(Club.id)
            .offset((page - 1) * 5)
            .limit(5)
        )
    ).all()
    return tuple(ClubOffer(c.id, c.name, c.budget, c.stadium_capacity, c.supporters) for c in rows)


async def ensure_entry_capacity(session: AsyncSession, at: datetime) -> bool:
    """Only inspect ready stock and enqueue replenishment; no generation in entry."""
    await allocation_lock(session)
    count = await available_clubs(session)
    if count <= LOW_WATER:
        await request_reserve(session, at)
    return count > 0


async def begin_game(
    session: AsyncSession,
    *,
    telegram_id: int,
    update_id: int,
    payload: str | None,
    at: datetime,
    policy: LeasePolicy = DEFAULT_LEASE_POLICY,
    internal: bool = False,
) -> Entry:
    # This lock precedes Manager, season, club and ownership locks in every entry mutation.
    await allocation_lock(session)
    await start_coverage(session, at)  # Explicit game-enabled entry, never the preview router.
    previous = await session.get(GameActionReceipt, (telegram_operation(update_id), 4))
    if previous is not None:
        actor = await actor_for(session, telegram_id, update_id, at)
        if previous.manager_id != actor.manager_id or previous.kind != "entry_opened":
            raise PermissionDenied("Entry operation belongs to another manager")
    else:
        start = await accept_start(
            session,
            telegram_id=telegram_id,
            update_id=update_id,
            payload=payload,
            at=at,
            internal=internal,
        )
        actor = Actor(
            start.manager_id,
            telegram_id,
            start.analytics_manager_id,
            start.operation_id,
            at,
            update_id,
        )
    manager = await session.get(Manager, actor.manager_id)
    if manager is None:
        raise ValueError("Manager disappeared")
    if internal:
        manager.is_internal = True
    # Operational dedupe must survive analytics opt-out/retention and a claim
    # made after an earlier /start. Retrying an old update never renews a lease.
    new_entry = previous is None
    if new_entry:
        session.add(
            GameActionReceipt(
                operation_id=actor.operation_id,
                effect_number=4,
                manager_id=actor.manager_id,
                ownership_id=None,
                club_id=None,
                kind="entry_opened",
                occurred_at=at,
            )
        )
        manager.bot_available = True
        if manager.bot_confirmed_at is None or manager.bot_confirmed_at < at:
            manager.bot_confirmed_at = at
        await session.flush()
    ownership = await session.scalar(
        select(OwnershipPeriod)
        .where(OwnershipPeriod.manager_id == actor.manager_id, OwnershipPeriod.ended_at.is_(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if ownership is not None and ownership.expires_at <= at:
        await end_expired(session, ownership, at)
        ownership = None
    if ownership is not None:
        ownership, club = await owned_club(session, actor)
        if new_entry:
            await user_effect(
                session,
                actor,
                "club_opened",
                Surface.CLUB,
                effect=3,
                ownership=ownership,
                club_id=club.id,
                policy=policy,
            )
        return Entry(actor, ownership.id, club.id, ())
    available = await ensure_entry_capacity(session, at)
    return Entry(actor, None, None, await offers(session) if available else (), not available)


async def preview_club(session: AsyncSession, actor: Actor, club_id: UUID) -> ClubOffer:
    await allocation_lock(session)
    active = (
        select(OwnershipPeriod.id)
        .where(OwnershipPeriod.club_id == Club.id, OwnershipPeriod.ended_at.is_(None))
        .exists()
    )
    club = await session.scalar(
        select(Club)
        .join(League, League.id == Club.league_id)
        .where(
            Club.id == club_id,
            League.tier == 5,
            Club.owner_telegram_id.is_(None),
            ~active,
            ready_set_predicate(),
        )
    )
    if club is None:
        raise ClubUnavailable("Club is no longer available")
    await user_effect(session, actor, "club_preview_opened", Surface.ONBOARDING, club_id=club.id)
    return ClubOffer(club.id, club.name, club.budget, club.stadium_capacity, club.supporters)


async def claim_club(
    session: AsyncSession,
    actor: Actor,
    club_id: UUID,
    policy: LeasePolicy = DEFAULT_LEASE_POLICY,
) -> OwnershipPeriod:
    await allocation_lock(session)
    manager = await session.scalar(
        select(Manager)
        .where(Manager.id == actor.manager_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if manager is None or manager.telegram_id != actor.telegram_id:
        raise PermissionDenied("Manager identity mismatch")
    key = f"claim:v1:{actor.operation_id}"
    previous = await session.scalar(
        select(OwnershipPeriod).where(OwnershipPeriod.operation_key == key)
    )
    if previous is not None:
        if (
            previous.manager_id != manager.id
            or previous.club_id != club_id
            or previous.ended_at is not None
            or previous.expires_at <= actor.at
        ):
            raise PermissionDenied("Stale or conflicting claim identity")
        return previous
    current = await session.scalar(
        select(OwnershipPeriod)
        .where(OwnershipPeriod.manager_id == manager.id, OwnershipPeriod.ended_at.is_(None))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if current is not None:
        if current.expires_at <= actor.at:
            await end_expired(session, current, actor.at)
        else:
            raise ClubUnavailable("Manager already owns a club")
    active = (
        select(OwnershipPeriod.id)
        .where(OwnershipPeriod.club_id == Club.id, OwnershipPeriod.ended_at.is_(None))
        .exists()
    )
    club = await session.scalar(
        select(Club)
        .join(League, League.id == Club.league_id)
        .where(
            Club.id == club_id,
            League.tier == 5,
            Club.owner_telegram_id.is_(None),
            ~active,
            ready_set_predicate(),
        )
        .with_for_update(of=Club)
        .execution_options(populate_existing=True)
    )
    if club is None:
        raise ClubUnavailable("Only a free tier-five club can be claimed")
    ownership = OwnershipPeriod(
        id=uuid5(actor.operation_id, "ownership"),
        operation_key=key,
        manager_id=manager.id,
        club_id=club.id,
        claimed_at=actor.at,
        last_game_action_at=actor.at,
        expires_at=ownership_deadline(actor.at),
        ended_at=None,
        end_reason=None,
    )
    session.add(ownership)
    club.owner_telegram_id = manager.telegram_id
    await session.flush()
    await user_effect(
        session,
        actor,
        "club_claimed",
        Surface.ONBOARDING,
        ownership=ownership,
        club_id=club.id,
        policy=policy,
    )
    if await available_clubs(session) <= LOW_WATER:
        await request_reserve(session, actor.at)
    return ownership
