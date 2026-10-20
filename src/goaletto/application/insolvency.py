"""Assess final committed assets, then release ownership atomically, without debt forgiveness."""

from datetime import datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.club_valuation import valuation
from goaletto.application.game_actions import allocation_lock
from goaletto.domain.club_valuation import POLICY, bankrupt
from goaletto.domain.game_cycle import midnight
from goaletto.infrastructure.models import (
    AcceptedCommand,
    Auction,
    AuctionBid,
    BankruptcyReceipt,
    Club,
    FinanceAccount,
    FriendlyInvitation,
    Manager,
    MoneyReservation,
    OutboxMessage,
    OwnershipPeriod,
    Season,
    SeasonClub,
    TrainingPlan,
)


async def assess(session: AsyncSession, club_id: UUID, at: datetime) -> None:
    await allocation_lock(session)
    club = await session.get(Club, club_id, with_for_update=True)
    account = await session.get(FinanceAccount, club_id, with_for_update=True)
    if club is None or account is None:
        return
    parts = await valuation(session, club)
    account.valuation, account.credit_limit = parts, sum(parts.values())
    owner = await session.scalar(
        select(OwnershipPeriod)
        .where(OwnershipPeriod.club_id == club_id, OwnershipPeriod.ended_at.is_(None))
        .with_for_update()
    )
    from goaletto.application.market import cancel_auction, cancel_ownership_auctions

    # Wages/upkeep may consume cash previously held for bids. Cancel uncovered bids.
    if account.reserved > max(0, club.budget):
        auctions = (
            await session.scalars(
                select(Auction)
                .join(AuctionBid, AuctionBid.id == Auction.highest_bid_id)
                .where(Auction.status == "open", AuctionBid.buyer_club_id == club_id)
                .order_by(Auction.person_id)
            )
        ).all()
        for auction in auctions:
            await cancel_auction(session, auction, "funds_changed", at)
    if account.reserved > max(0, club.budget):
        from goaletto.application.money import release

        remaining = (
            await session.scalars(
                select(MoneyReservation)
                .where(MoneyReservation.club_id == club_id, MoneyReservation.status == "active")
                .order_by(MoneyReservation.id)
            )
        ).all()
        for hold in remaining:
            await release(session, club_id, hold.id, at)
    if club.budget >= 0:
        club.bankrupt_at = None
    if not bankrupt(club.budget, account.credit_limit) or club.bankrupt_at is not None:
        return
    club.bankrupt_at = at
    receipt_id = uuid5(club_id, f"bankruptcy:v1:{account.sequence}")
    session.add(
        BankruptcyReceipt(
            id=receipt_id,
            club_id=club_id,
            ownership_id=owner.id if owner else None,
            occurred_at=at,
            balance=club.budget,
            assets=account.credit_limit,
            policy=POLICY,
            valuation=parts,
        )
    )
    if owner is None:
        return
    owner.ended_at, owner.end_reason = at, "bankrupt"
    club.owner_telegram_id = None
    await session.flush()
    await cancel_ownership_auctions(session, owner.id, at)
    from goaletto.application.scouting import cancel_invalid

    await cancel_invalid(session, club_id, at)
    await session.execute(
        update(AcceptedCommand)
        .where(AcceptedCommand.ownership_id == owner.id, AcceptedCommand.status == "accepted")
        .values(status="cancelled")
    )
    await session.execute(
        update(TrainingPlan)
        .where(TrainingPlan.ownership_id == owner.id, TrainingPlan.status == "scheduled")
        .values(status="cancelled")
    )
    await session.execute(
        update(FriendlyInvitation)
        .where(
            FriendlyInvitation.status == "pending",
            (FriendlyInvitation.challenger_ownership_id == owner.id)
            | (FriendlyInvitation.challenged_ownership_id == owner.id),
        )
        .values(status="cancelled", responded_at=at)
    )
    manager = await session.get(Manager, owner.manager_id)
    assert manager is not None
    season = await session.scalar(
        select(Season)
        .join(SeasonClub, SeasonClub.season_id == Season.id)
        .where(SeasonClub.club_id == club_id)
        .order_by(Season.starts_at.desc())
        .limit(1)
    )
    # A bankrupt manager cannot repeatedly raid fresh clubs within one season.
    boundary = season.ends_at + timedelta(days=8) if season else at + timedelta(days=80)
    if season and season.cycle_epoch and midnight(season.cycle_epoch) > at:
        boundary = midnight(season.cycle_epoch)
    manager.bankruptcy_until = max(at + timedelta(days=1), boundary)
    session.add(
        OutboxMessage(
            id=uuid5(receipt_id, "notice"),
            dedupe_key=f"bankruptcy:{receipt_id}",
            chat_id=manager.telegram_id,
            payload={
                "version": 1,
                "kind": "bankruptcy",
                "ownership_id": str(owner.id),
                "receipt_id": str(receipt_id),
                "club_name": club.name,
                "balance": club.budget,
                "assets": account.credit_limit,
            },
            due_at=at,
            priority=100,
        )
    )
    await session.flush()
