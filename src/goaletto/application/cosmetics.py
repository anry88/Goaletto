"""Durable single-slot rename grants and calendar-month club VIP; no Telegram IO."""

from datetime import datetime
from typing import cast
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.academy import current_season
from goaletto.application.game_actions import allocation_lock
from goaletto.domain.game_cycle import midnight
from goaletto.infrastructure.models import (
    ClubNameChoice,
    ClubRenameReceipt,
    CosmeticGrant,
    OwnershipPeriod,
    Season,
    StarOrder,
    StarPayment,
)


def offseason(season: Season, at: datetime) -> bool:
    return (
        season.status == "scheduled"
        and season.cycle_epoch is not None
        and at < midnight(season.cycle_epoch)
    )


async def vip_payment(session: AsyncSession, club: UUID, at: datetime) -> StarPayment | None:
    return cast(
        StarPayment | None,
        await session.scalar(
            select(StarPayment)
            .where(
                StarPayment.club_id == club,
                StarPayment.product == "vip",
                StarPayment.status == "delivered",
                StarPayment.refunded_at.is_(None),
                StarPayment.received_at <= at,
                StarPayment.vip_until > at,
            )
            .order_by(StarPayment.received_at.desc())
            .limit(1)
        ),
    )


async def vip_clubs(session: AsyncSession, clubs: set[UUID], at: datetime) -> frozenset[UUID]:
    if not clubs:
        return frozenset()
    rows = (
        await session.scalars(
            select(StarPayment.club_id).where(
                StarPayment.club_id.in_(clubs),
                StarPayment.product == "vip",
                StarPayment.status == "delivered",
                StarPayment.refunded_at.is_(None),
                StarPayment.received_at <= at,
                StarPayment.vip_until > at,
            )
        )
    ).all()
    return frozenset(v for v in rows if v is not None)


async def available_grant(session: AsyncSession, club: UUID) -> CosmeticGrant | None:
    return cast(
        CosmeticGrant | None,
        await session.scalar(
            select(CosmeticGrant)
            .where(
                CosmeticGrant.club_id == club,
                CosmeticGrant.status == "available",
            )
            .with_for_update()
        ),
    )


async def ensure_initial(session: AsyncSession, owner: OwnershipPeriod, at: datetime) -> None:
    await allocation_lock(session)
    key = uuid5(owner.id, "initial-rename-v2")
    if await session.get(CosmeticGrant, key):
        return
    old = await session.get(ClubNameChoice, owner.id)
    used = old is not None and old.changed_at is not None
    occupied = await available_grant(session, owner.club_id)
    session.add(
        CosmeticGrant(
            id=key,
            club_id=owner.club_id,
            ownership_id=owner.id,
            origin="initial",
            status="consumed" if used else "skipped" if occupied else "available",
            created_at=owner.claimed_at,
            consumed_at=old.changed_at if old is not None and used else None,
        )
    )
    if used and old is not None:
        assert old.original_name is not None and old.candidate is not None
        session.add(
            ClubRenameReceipt(
                id=uuid5(owner.id, "legacy-rename-v1"),
                ownership_id=owner.id,
                club_id=owner.club_id,
                grant_id=key,
                old_name=old.original_name,
                new_name=old.candidate,
                changed_at=old.changed_at,
            )
        )
    await session.flush()


async def ensure_vip_bonus(session: AsyncSession, club: UUID, at: datetime) -> None:
    await allocation_lock(session)
    season = await current_season(session, club)
    if not offseason(season, at) or await available_grant(session, club):
        return
    key = uuid5(club, f"vip-rename:{season.id}")
    if await session.get(CosmeticGrant, key):
        return
    paid = await vip_payment(session, club, at)
    if paid is None:
        return
    held = await session.scalar(
        select(StarOrder.id)
        .where(
            StarOrder.club_id == club,
            StarOrder.product == "rename",
            StarOrder.status == "approved",
            StarOrder.expires_at > at,
        )
        .limit(1)
    )
    if held:
        return
    session.add(
        CosmeticGrant(
            id=key,
            club_id=club,
            payment_id=paid.id,
            season_id=season.id,
            origin="vip",
            status="available",
            created_at=at,
        )
    )
    await session.flush()
