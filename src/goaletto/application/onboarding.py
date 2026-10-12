"""Tier-five club entry and atomic world expansion. No Telegram or network calls."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.acquisition import accept_start
from goaletto.application.analytics import Surface, require_utc
from goaletto.application.game_actions import (
    DEFAULT_LEASE_POLICY,
    Actor,
    LeasePolicy,
    PermissionDenied,
    allocation_lock,
    end_expired,
    owned_club,
    user_effect,
)
from goaletto.application.match_runtime import activate_season
from goaletto.application.persons import AppearanceExhausted
from goaletto.application.world import initialize_world
from goaletto.domain.ownership import ownership_deadline
from goaletto.domain.world import WorldOptions, stable_seed
from goaletto.infrastructure.analytics import start_coverage
from goaletto.infrastructure.models import Club, League, LeagueSet, Manager, OwnershipPeriod


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
    days = (5 - at.weekday()) % 7
    kickoff = (at + timedelta(days=days)).replace(hour=18, minute=0, second=0, microsecond=0)
    if kickoff - timedelta(minutes=5, seconds=1) <= at:
        kickoff += timedelta(weeks=1)
    return kickoff


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
            )
            .order_by(Club.id)
            .offset((page - 1) * 5)
            .limit(5)
        )
    ).all()
    return tuple(ClubOffer(c.id, c.name, c.budget, c.stadium_capacity, c.supporters) for c in rows)


async def ensure_entry_capacity(session: AsyncSession, at: datetime) -> bool:
    """Under the shared allocation lock. Failed catalog preflight creates no partial set."""
    await allocation_lock(session)
    if await offers(session):
        return True
    count = await session.scalar(select(func.count()).select_from(LeagueSet))
    if count is None:
        raise ValueError("Could not count league sets")
    key = "initial" if count == 0 else f"auto-{count + 1:04d}"
    options = WorldOptions(
        key=key,
        seed=0 if count == 0 else stable_seed(0, key),
        first_kickoff=next_initial_kickoff(at),
    )
    try:
        async with session.begin_nested():
            result = await initialize_world(session, options, initialized_at=at)
            await activate_season(session, result.season_id, at)
    except AppearanceExhausted:
        return False
    return bool(await offers(session))


async def begin_game(
    session: AsyncSession,
    *,
    telegram_id: int,
    update_id: int,
    payload: str | None,
    at: datetime,
    policy: LeasePolicy = DEFAULT_LEASE_POLICY,
) -> Entry:
    # This lock precedes Manager, season, club and ownership locks in every entry mutation.
    await allocation_lock(session)
    await start_coverage(session, at)  # Explicit game-enabled entry, never the preview router.
    start = await accept_start(
        session, telegram_id=telegram_id, update_id=update_id, payload=payload, at=at
    )
    actor = Actor(
        start.manager_id, telegram_id, start.analytics_manager_id, start.operation_id, at, update_id
    )
    manager = await session.get(Manager, actor.manager_id)
    if manager is None:
        raise ValueError("Manager disappeared")
    manager.bot_available = True
    if manager.bot_confirmed_at is None or manager.bot_confirmed_at < at:
        manager.bot_confirmed_at = at
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
    return ownership
