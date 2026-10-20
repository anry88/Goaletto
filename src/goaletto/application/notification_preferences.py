"""Reminder preference reads and mutations. No Telegram, network or wall-clock reads.

The caller owns the transaction and commits before any Telegram I/O. Reads and
mutations recheck the manager and the current ownership under lock, so a stale
button from a previous ownership cannot change the new owner's settings.
Mutations never extend the ownership deadline, never touch bot availability
(which is confirmed only by the game /start path), and never emit journey
events: operational dedupe lives in game_action_receipts and works with or
without analytics coverage/opt-out.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.game_actions import (
    DEFAULT_LEASE_POLICY,
    Actor,
    LeasePolicy,
    PermissionDenied,
    allocation_lock,
    schedule_lease,
)
from goaletto.infrastructure.models import (
    Club,
    ClubNameChoice,
    GameActionReceipt,
    Manager,
    OwnershipPeriod,
)

SETTINGS_KIND = "notification_preferences"


@dataclass(frozen=True)
class SettingsState:
    manager_id: UUID
    telegram_id: int
    reminders_enabled: bool
    bot_available: bool
    has_ownership: bool
    ownership_id: UUID | None
    club_id: UUID | None
    club_name: str | None
    expires_at: datetime | None
    last_game_action_at: datetime | None
    locale: str = "ru"
    timezone_name: str = "UTC"
    preferred_kickoff_minute: int | None = None
    observed_at: datetime | None = None
    rename_available: bool = False
    rename_offseason: bool = False
    rename_initial: bool = False
    vip_until: datetime | None = None


@dataclass(frozen=True)
class PreferenceOutcome:
    applied: bool
    duplicate: bool
    reminders_enabled: bool
    ownership_id: UUID
    club_id: UUID


def _effect_number(enabled: bool) -> int:
    return 1 if enabled else 0


async def _locked_manager(session: AsyncSession, actor: Actor) -> Manager:
    manager = await session.scalar(
        select(Manager)
        .where(Manager.id == actor.manager_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if manager is None or manager.telegram_id != actor.telegram_id:
        raise PermissionDenied("Manager identity does not match the actor")
    return manager


async def get_settings(session: AsyncSession, actor: Actor) -> SettingsState:
    """Current reminder state. Works with or without an active ownership."""
    if not session.in_transaction():
        raise ValueError("Notification settings need a transaction")
    await allocation_lock(session)
    manager = await _locked_manager(session, actor)
    ownership = await session.scalar(
        select(OwnershipPeriod)
        .where(
            OwnershipPeriod.manager_id == actor.manager_id,
            OwnershipPeriod.ended_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if ownership is None or ownership.expires_at <= actor.at:
        return SettingsState(
            locale=manager.locale,
            timezone_name=manager.timezone_name,
            preferred_kickoff_minute=manager.preferred_kickoff_minute,
            observed_at=actor.at,
            manager_id=manager.id,
            telegram_id=manager.telegram_id,
            reminders_enabled=bool(manager.reminders_enabled),
            bot_available=bool(manager.bot_available),
            has_ownership=False,
            ownership_id=None,
            club_id=None,
            club_name=None,
            expires_at=None,
            last_game_action_at=None,
        )
    club = await session.scalar(select(Club).where(Club.id == ownership.club_id).with_for_update())
    if club is None or club.owner_telegram_id != actor.telegram_id:
        raise PermissionDenied("Current club ownership does not match the actor")
    from goaletto.application.academy import current_season
    from goaletto.application.cosmetics import (
        available_grant,
        ensure_initial,
        ensure_vip_bonus,
        initial_rename,
        offseason,
        vip_payment,
    )

    await ensure_initial(session, ownership, actor.at)
    await ensure_vip_bonus(session, club.id, actor.at)
    grant = await available_grant(session, club.id)
    vip = await vip_payment(session, club.id, actor.at)
    season = await current_season(session, club.id)
    return SettingsState(
        rename_available=grant is not None,
        rename_initial=initial_rename(
            grant, await session.get(ClubNameChoice, ownership.id), ownership.id
        ),
        rename_offseason=offseason(season, actor.at),
        vip_until=vip.vip_until if vip else None,
        locale=manager.locale,
        timezone_name=manager.timezone_name,
        preferred_kickoff_minute=manager.preferred_kickoff_minute,
        observed_at=actor.at,
        manager_id=manager.id,
        telegram_id=manager.telegram_id,
        reminders_enabled=bool(manager.reminders_enabled),
        bot_available=bool(manager.bot_available),
        has_ownership=True,
        ownership_id=ownership.id,
        club_id=club.id,
        club_name=club.name,
        expires_at=ownership.expires_at,
        last_game_action_at=ownership.last_game_action_at,
    )


async def set_reminders_enabled(
    session: AsyncSession,
    actor: Actor,
    *,
    enabled: bool,
    ownership_id: UUID,
    policy: LeasePolicy = DEFAULT_LEASE_POLICY,
    clock: Callable[[], datetime] | None = None,
) -> PreferenceOutcome:
    """Set the desired reminder value explicitly. Retries never toggle it back.

    The callback carries the desired value (not a toggle) and the ownership it
    was rendered for. Stale, expired, or foreign ownerships are rejected before
    any mutation. The ownership deadline is preserved; bot availability and
    analytics are untouched.
    """
    if not session.in_transaction():
        raise ValueError("Notification settings need a transaction")
    if type(enabled) is not bool:
        raise ValueError("Reminder preference must be a boolean")
    if not isinstance(ownership_id, UUID):
        raise ValueError("Ownership identity must be a UUID")
    if not isinstance(policy, LeasePolicy):
        raise ValueError("Invalid lease policy")
    await allocation_lock(session)
    manager = await _locked_manager(session, actor)
    ownership = await session.scalar(
        select(OwnershipPeriod)
        .where(OwnershipPeriod.id == ownership_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        ownership is None
        or ownership.manager_id != actor.manager_id
        or ownership.ended_at is not None
        or ownership.expires_at <= actor.at
    ):
        raise PermissionDenied("Settings require the current ownership")
    current = await session.scalar(
        select(OwnershipPeriod)
        .where(
            OwnershipPeriod.manager_id == actor.manager_id,
            OwnershipPeriod.ended_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if current is None or current.id != ownership.id:
        raise PermissionDenied("Stale settings button from a previous ownership")
    club = await session.scalar(select(Club).where(Club.id == ownership.club_id).with_for_update())
    if club is None or club.owner_telegram_id != actor.telegram_id:
        raise PermissionDenied("Current club ownership does not match the actor")
    checked_at = max(actor.at, clock() if clock is not None else datetime.now(UTC))
    if ownership.expires_at <= checked_at:
        raise PermissionDenied("Ownership expired while waiting for settings locks")
    effect = _effect_number(enabled)
    previous = await session.get(GameActionReceipt, (actor.operation_id, effect))
    if previous is not None:
        if (
            previous.manager_id != actor.manager_id
            or previous.kind != SETTINGS_KIND
            or previous.club_id != club.id
            or previous.ownership_id != ownership.id
        ):
            raise PermissionDenied("Action identity was reused with a different scope")
        return PreferenceOutcome(
            applied=False,
            duplicate=True,
            reminders_enabled=bool(manager.reminders_enabled),
            ownership_id=ownership.id,
            club_id=club.id,
        )
    if await session.get(GameActionReceipt, (actor.operation_id, 1 - effect)) is not None:
        raise PermissionDenied("Settings operation was reused with a different value")
    session.add(
        GameActionReceipt(
            operation_id=actor.operation_id,
            effect_number=effect,
            manager_id=actor.manager_id,
            ownership_id=ownership.id,
            club_id=club.id,
            kind=SETTINGS_KIND,
            occurred_at=actor.at,
        )
    )
    await session.flush()
    previous_value = bool(manager.reminders_enabled)
    changed = previous_value != enabled
    if changed:
        manager.reminders_enabled = enabled
        await session.flush()
    # Restore the actual reminder schedule on enable; on disable refresh the
    # expiry lease while leaving stale remind jobs inert via sender guards.
    # schedule_lease never touches last_game_action_at/expires_at: the six-month
    # deadline is preserved. bot_available is never set here.
    await schedule_lease(session, ownership, policy)
    return PreferenceOutcome(
        applied=changed,
        duplicate=False,
        reminders_enabled=enabled,
        ownership_id=ownership.id,
        club_id=club.id,
    )
