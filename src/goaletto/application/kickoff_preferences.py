"""Explicit durable settings and future scheduling adapter; never reschedules fixtures."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.application.game_actions import Actor, PermissionDenied, allocation_lock
from goaletto.domain.kickoff import (
    BASELINE_MINUTE,
    KickoffDecision,
    KickoffPreference,
    choose_kickoff,
    validate_zone,
)
from goaletto.infrastructure.models import (
    KickoffDecisionReceipt,
    KickoffPreferenceReceipt,
    Manager,
    OwnershipPeriod,
)


async def set_kickoff_preference(
    session: AsyncSession, actor: Actor, *, zone: str | None = None, minute: int | None = None
) -> Manager:
    if not session.in_transaction() or (zone is None and minute is None):
        raise ValueError("Explicit preference needs a transaction")
    if zone is not None:
        validate_zone(zone)
    if minute is not None and (type(minute) is not int or not 0 <= minute < 1440):
        raise ValueError("Invalid preferred minute")
    await allocation_lock(session)
    manager = await session.scalar(
        select(Manager)
        .where(Manager.id == actor.manager_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if manager is None or manager.telegram_id != actor.telegram_id:
        raise PermissionDenied("Manager identity mismatch")
    receipt = await session.get(KickoffPreferenceReceipt, actor.operation_id)
    if receipt is not None:
        if (receipt.manager_id, receipt.timezone_name, receipt.preferred_minute) != (
            manager.id,
            zone,
            minute,
        ):
            raise PermissionDenied("Preference operation reused")
        return manager  # Old retry must never undo a later setting.
    session.add(
        KickoffPreferenceReceipt(
            operation_id=actor.operation_id,
            manager_id=manager.id,
            timezone_name=zone,
            preferred_minute=minute,
            occurred_at=actor.at,
        )
    )
    if zone is not None:
        manager.timezone_name = zone
    if minute is not None:
        manager.preferred_kickoff_minute = minute
    await session.flush()
    return manager


async def fixture_kickoff_decision(
    session: AsyncSession,
    *,
    fixture_key: UUID,
    day: date,
    home_club_id: UUID,
    away_club_id: UUID,
    at: datetime,
    seed: int,
) -> KickoffDecision:
    """Persist the scheduling decision once; used by the AND-121 horizon materializer.

    Bots contribute no clock, an owner without a preference uses 18:00 UTC.
    Caller invokes only for fixtures entering the announcement horizon.
    Existing materialized fixtures continue to use their stored timestamp.
    """
    require_utc(at)
    if not session.in_transaction():
        raise ValueError("Kickoff decision needs a transaction")
    await allocation_lock(session)
    receipt = await session.get(KickoffDecisionReceipt, fixture_key)
    if receipt is not None:
        if (receipt.home_club_id, receipt.away_club_id, receipt.assigned_day, receipt.seed) != (
            home_club_id,
            away_club_id,
            day,
            seed,
        ):
            raise ValueError("Kickoff decision identity reused")
        return KickoffDecision(receipt.kickoff, receipt.policy_version, receipt.tie_break)
    decision = await preview_fixture_kickoff(
        session, day=day, home_club_id=home_club_id, away_club_id=away_club_id, at=at, seed=seed
    )
    session.add(
        KickoffDecisionReceipt(
            fixture_key=fixture_key,
            home_club_id=home_club_id,
            away_club_id=away_club_id,
            assigned_day=day,
            seed=seed,
            kickoff=decision.kickoff,
            policy_version=decision.policy_version,
            tie_break=decision.tie_break,
            decided_at=at,
        )
    )
    await session.flush()
    return decision


async def preview_fixture_kickoff(
    session: AsyncSession,
    *,
    day: date,
    home_club_id: UUID,
    away_club_id: UUID,
    at: datetime,
    seed: int,
) -> KickoffDecision:
    """Read preferences in the caller's allocation lock, without pinning a result."""
    preferences: list[KickoffPreference | None] = []
    for club_id in (home_club_id, away_club_id):
        manager = await session.scalar(
            select(Manager)
            .join(OwnershipPeriod)
            .where(
                OwnershipPeriod.club_id == club_id,
                OwnershipPeriod.ended_at.is_(None),
                OwnershipPeriod.expires_at > at,
            )
        )
        preferences.append(
            KickoffPreference(manager.timezone_name, manager.preferred_kickoff_minute)
            if manager is not None and manager.preferred_kickoff_minute is not None
            else KickoffPreference("UTC", BASELINE_MINUTE)
            if manager is not None
            else None
        )
    return choose_kickoff(day, *preferences, seed=seed)
