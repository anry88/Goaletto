"""Durable within-calendar auctions. Caller owns transaction; no Telegram calls."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface, require_utc
from goaletto.application.finances import close_salary, salary_contract
from goaletto.application.game_actions import (
    Actor,
    PermissionDenied,
    allocation_lock,
    owned_club,
    user_effect,
)
from goaletto.application.money import InsufficientFunds, locked_accounts, release, reserve, settle
from goaletto.domain.finances import MAX_MONEY, money, player_wage
from goaletto.domain.market import (
    AUCTION_HOURS,
    MIN_PRICE,
    POLICY,
    MarketWindow,
    extended_deadline,
    market_window,
    minimum_bid,
)
from goaletto.domain.world import SKILL_NAMES
from goaletto.infrastructure.models import (
    Auction,
    AuctionBid,
    Club,
    ClubLineup,
    Fixture,
    Job,
    League,
    LineupSlot,
    Manager,
    MoneyReservation,
    OutboxMessage,
    OwnershipPeriod,
    Person,
    Player,
    Season,
    SeasonClub,
    TransferReceipt,
)


async def league_set_for(session: AsyncSession, club: Club) -> UUID:
    key = await session.scalar(select(League.league_set_id).where(League.id == club.league_id))
    if key is None:
        raise ValueError("Missing club league set")
    return key


async def calendar_for(
    session: AsyncSession, club_id: UUID, at: datetime
) -> tuple[Season, MarketWindow]:
    season = await session.scalar(
        select(Season)
        .join(SeasonClub, SeasonClub.season_id == Season.id)
        .where(
            SeasonClub.club_id == club_id, Season.status.in_(("scheduled", "active", "finished"))
        )
        .order_by(Season.number.desc())
        .limit(1)
    )
    if season is None:
        raise ValueError("Club calendar unavailable")
    return season, market_window(season.starts_at, season.ends_at, at)


async def owner_valid(
    session: AsyncSession, ownership_id: UUID, club_id: UUID, at: datetime
) -> bool:
    owner = await session.get(OwnershipPeriod, ownership_id)
    club = await session.get(Club, club_id)
    manager = await session.get(Manager, owner.manager_id) if owner else None
    return bool(
        owner
        and club
        and manager
        and owner.club_id == club.id
        and owner.ended_at is None
        and owner.claimed_at <= at < owner.expires_at
        and club.owner_telegram_id == manager.telegram_id
    )


async def active_count(session: AsyncSession, club_id: UUID) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(Player)
            .where(Player.club_id == club_id, Player.status.in_(("active", "last_season")))
        )
        or 0
    )


async def player_locked(session: AsyncSession, person_id: UUID) -> tuple[Player, Person]:
    player = await session.scalar(
        select(Player)
        .where(Player.person_id == person_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    person = await session.scalar(
        select(Person)
        .where(Person.id == person_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if player is None or person is None:
        raise ValueError("Missing auction person")
    return player, person


async def leading(session: AsyncSession, auction: Auction) -> AuctionBid | None:
    row = await session.get(AuctionBid, auction.highest_bid_id) if auction.highest_bid_id else None
    if auction.highest_bid_id and (row is None or row.auction_id != auction.id):
        raise ValueError("Invalid highest bid")
    return row


async def notify(
    session: AsyncSession,
    auction: Auction,
    event: str,
    ownership_id: UUID,
    club_id: UUID,
    at: datetime,
    bid: AuctionBid | None = None,
) -> None:
    owner = await session.get(OwnershipPeriod, ownership_id)
    manager = await session.get(Manager, owner.manager_id) if owner else None
    person = await session.get(Person, auction.person_id)
    if manager is None or person is None:
        return
    key = f"market:v1:{auction.id}:{event}:{bid.id if bid else '-'}:{ownership_id}"
    await session.execute(
        insert(OutboxMessage)
        .values(
            id=uuid5(auction.id, key),
            dedupe_key=key,
            chat_id=manager.telegram_id,
            payload={
                "version": 1,
                "kind": "market_notice",
                "auction_id": str(auction.id),
                "ownership_id": str(ownership_id),
                "club_id": str(club_id),
                "person_id": str(person.id),
                "player_name": person.name,
                "event": event,
                "bid_id": str(bid.id) if bid else None,
                "amount": bid.amount if bid else 0,
                "weekly_rate": auction.weekly_rate,
                "reason": auction.close_reason,
            },
            due_at=at,
            status="pending",
            attempts=0,
            priority=70,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )


async def seller_safe(session: AsyncSession, club_id: UUID, player: Player) -> bool:
    count = await active_count(session, club_id)
    outgoing = (
        await session.scalar(
            select(func.count())
            .select_from(Auction)
            .where(
                Auction.seller_club_id == club_id,
                Auction.status == "open",
                Auction.person_id != player.person_id,
            )
        )
        or 0
    )
    if count - outgoing - 1 < 16:
        return False
    if player.position == "GK":
        gks = (
            await session.scalar(
                select(func.count())
                .select_from(Player)
                .where(
                    Player.club_id == club_id,
                    Player.position == "GK",
                    Player.status.in_(("active", "last_season")),
                )
            )
            or 0
        )
        listed = (
            await session.scalar(
                select(func.count())
                .select_from(Auction)
                .join(Player, Player.person_id == Auction.person_id)
                .where(
                    Auction.seller_club_id == club_id,
                    Auction.status == "open",
                    Auction.person_id != player.person_id,
                    Player.position == "GK",
                )
            )
            or 0
        )
        if gks - listed - 1 < 1:
            return False
    return True


async def incoming_count(
    session: AsyncSession, club_id: UUID, excluding: UUID | None = None
) -> int:
    query = (
        select(func.count())
        .select_from(Auction)
        .join(AuctionBid, AuctionBid.id == Auction.highest_bid_id)
        .where(Auction.status == "open", AuctionBid.buyer_club_id == club_id)
    )
    if excluding is not None:
        query = query.where(Auction.id != excluding)
    return await session.scalar(query) or 0


async def list_player(
    session: AsyncSession,
    actor: Actor,
    ownership_id: UUID,
    person_id: UUID,
    price: int,
    *,
    clock: Callable[[], datetime] | None = None,
) -> Auction:
    money(price)
    if price < MIN_PRICE:
        raise ValueError("Minimum listing price is 100K")
    owner, club = await owned_club(session, actor)
    at = max(actor.at, clock() if clock else datetime.now(UTC))
    require_utc(at)
    if owner.id != ownership_id or not await owner_valid(session, owner.id, club.id, at):
        raise PermissionDenied("Listing requires current owner")
    previous = await session.scalar(
        select(Auction).where(Auction.operation_id == actor.operation_id)
    )
    if previous:
        if (previous.person_id, previous.seller_ownership_id, previous.initial_price) != (
            person_id,
            owner.id,
            price,
        ):
            raise ValueError("Listing operation reused")
        return previous
    season, window = await calendar_for(session, club.id, at)
    if not window.contains(at):
        raise ValueError("Transfer window closed")
    player, person = await player_locked(session, person_id)
    if (
        player.club_id != club.id
        or player.status != "active"
        or person.career != "player"
        or person.age_applied_for_season_id != season.id
    ):
        raise ValueError("Player cannot be traded")
    if await session.scalar(
        select(Auction.id).where(Auction.person_id == person_id, Auction.status == "open")
    ):
        raise ValueError("Player already listed")
    if not await seller_safe(session, club.id, player):
        raise ValueError("Sale would violate roster or goalkeeper minimum")
    auction = Auction(
        id=uuid5(club.id, f"auction:v1:{actor.operation_id}"),
        operation_id=actor.operation_id,
        person_id=person_id,
        seller_club_id=club.id,
        seller_ownership_id=owner.id,
        season_id=season.id,
        league_set_id=await league_set_for(session, club),
        player_age=person.age,
        initial_price=price,
        weekly_rate=player_wage(tuple(getattr(player, k) for k in SKILL_NAMES[:8])),
        created_at=at,
        ends_at=at + timedelta(hours=AUCTION_HOURS),
        status="open",
        policy_version=POLICY,
    )
    session.add(auction)
    await session.flush()
    await session.execute(
        insert(Job)
        .values(
            id=uuid5(auction.id, "close-job"),
            dedupe_key=f"auction:v1:{auction.id}:close",
            kind="market.close",
            payload={"version": 1, "auction_id": str(auction.id)},
            due_at=auction.ends_at,
            status="pending",
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )
    await user_effect(
        session,
        replace(actor, at=at),
        "market_listing_created",
        Surface.CLUB,
        ownership=owner,
        club_id=club.id,
    )
    return auction


async def cancel_auction(
    session: AsyncSession, auction: Auction, reason: str, at: datetime
) -> bool:
    require_utc(at)
    await allocation_lock(session)
    await player_locked(session, auction.person_id)
    fresh = await session.scalar(
        select(Auction)
        .where(Auction.id == auction.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if fresh is None:
        raise ValueError("Missing auction")
    auction = fresh
    if reason not in {
        "last_season",
        "retired",
        "ownership_changed",
        "roster_changed",
        "calendar_changed",
        "withdrawn",
        "operator",
        "funds_changed",
    }:
        raise ValueError("Invalid cancellation reason")
    if auction.status != "open":
        return False
    bid = await leading(session, auction)
    if bid:
        await release(session, bid.buyer_club_id, bid.reservation_id, at)
    auction.status, auction.close_reason, auction.closed_at = "cancelled", reason, at
    await session.execute(
        update(Job)
        .where(
            Job.kind == "market.close",
            Job.payload["auction_id"].astext == str(auction.id),
            Job.status.in_(("pending", "held")),
        )
        .values(status="done")
    )
    await notify(
        session, auction, "cancelled", auction.seller_ownership_id, auction.seller_club_id, at, bid
    )
    if bid:
        await notify(
            session, auction, "cancelled", bid.buyer_ownership_id, bid.buyer_club_id, at, bid
        )
    await session.flush()
    return True


async def prohibit_player_trades(session: AsyncSession, person_id: UUID, at: datetime) -> int:
    """Retirement caller sets the persisted flag under this same player lock/transaction."""
    require_utc(at)
    await allocation_lock(session)
    player, _ = await player_locked(session, person_id)
    if player.status not in {"last_season", "retired"}:
        raise ValueError("Player is not trade-prohibited")
    rows = (
        await session.scalars(
            select(Auction)
            .where(Auction.person_id == person_id, Auction.status == "open")
            .with_for_update()
        )
    ).all()
    for row in rows:
        await cancel_auction(
            session, row, "last_season" if player.status == "last_season" else "retired", at
        )
    return len(rows)


async def cancel_ownership_auctions(
    session: AsyncSession, ownership_id: UUID, at: datetime
) -> None:
    await allocation_lock(session)
    ids = (
        await session.scalars(
            select(Auction.id)
            .outerjoin(AuctionBid, AuctionBid.id == Auction.highest_bid_id)
            .where(
                Auction.status == "open",
                (Auction.seller_ownership_id == ownership_id)
                | (AuctionBid.buyer_ownership_id == ownership_id),
            )
            .order_by(Auction.person_id)
        )
    ).all()
    for key in ids:
        auction = await session.get(Auction, key)
        if auction:
            await player_locked(session, auction.person_id)
            await cancel_auction(session, auction, "ownership_changed", at)


async def invalid_reason(
    session: AsyncSession, auction: Auction, player: Player, person: Person, at: datetime
) -> str | None:
    if player.status != "active" or person.career != "player":
        return "last_season" if player.status == "last_season" else "retired"
    if player.club_id != auction.seller_club_id:
        return "roster_changed"
    if person.age != auction.player_age or person.age_applied_for_season_id != auction.season_id:
        return "calendar_changed"
    if not await owner_valid(session, auction.seller_ownership_id, auction.seller_club_id, at):
        return "ownership_changed"
    if not await seller_safe(session, auction.seller_club_id, player):
        return "roster_changed"
    return None


@dataclass(frozen=True)
class BidResult:
    accepted: bool
    reason: str | None = None


async def place_bid(
    session: AsyncSession,
    actor: Actor,
    ownership_id: UUID,
    auction_id: UUID,
    amount: int,
    *,
    clock: Callable[[], datetime] | None = None,
) -> BidResult:
    money(amount)
    owner, club = await owned_club(session, actor)
    at = max(actor.at, clock() if clock else datetime.now(UTC))
    require_utc(at)
    if owner.id != ownership_id or not await owner_valid(session, owner.id, club.id, at):
        raise PermissionDenied("Bid requires current owner")
    auction = await session.scalar(
        select(Auction)
        .where(Auction.id == auction_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        auction is None
        or auction.league_set_id != await league_set_for(session, club)
        or auction.seller_club_id == club.id
    ):
        raise ValueError("Auction outside club calendar or own player")
    player, person = await player_locked(session, auction.person_id)
    at = max(at, clock() if clock else datetime.now(UTC))
    require_utc(at)
    if not await owner_valid(session, owner.id, club.id, at):
        raise PermissionDenied("Ownership expired while bidding")
    prior = await session.scalar(
        select(AuctionBid).where(AuctionBid.operation_id == actor.operation_id)
    )
    if prior:
        if (prior.auction_id, prior.buyer_ownership_id, prior.amount) != (
            auction.id,
            owner.id,
            amount,
        ):
            raise ValueError("Bid operation reused")
        return BidResult(True)
    if auction.status != "open" or at >= auction.ends_at:
        raise ValueError("Auction closed")
    why = await invalid_reason(session, auction, player, person, at)
    if why:
        await cancel_auction(session, auction, why, at)
        return BidResult(False, why)
    season, window = await calendar_for(session, club.id, at)
    if season.id != auction.season_id or not window.contains(at):
        raise ValueError("Transfer window closed or different calendar year")
    old = await leading(session, auction)
    if amount < minimum_bid(auction.initial_price, old.amount if old else None):
        raise ValueError("Bid below current minimum")
    if (
        await active_count(session, club.id) + await incoming_count(session, club.id, auction.id)
        >= 25
    ):
        raise ValueError("Incoming roster slots exhausted")
    accounts = await locked_accounts(session, club.id, auction.seller_club_id)
    account = accounts[club.id]
    own_hold = old.amount if old and old.buyer_club_id == club.id else 0
    if account.record.arrears or amount > account.available + own_hold:
        raise InsufficientFunds("Insufficient bid funds")
    if accounts[auction.seller_club_id].club.budget > MAX_MONEY - amount:
        raise InsufficientFunds("Seller cannot receive this amount")
    bid_id = uuid5(club.id, f"bid:v1:{actor.operation_id}")
    reservation_id = uuid5(bid_id, "reservation")
    if old:
        await release(session, old.buyer_club_id, old.reservation_id, at)
    await reserve(
        session,
        club.id,
        reservation_id,
        amount,
        {"auction_id": str(auction.id), "bid_id": str(bid_id)},
        at,
    )
    bid = AuctionBid(
        id=bid_id,
        operation_id=actor.operation_id,
        auction_id=auction.id,
        buyer_club_id=club.id,
        buyer_ownership_id=owner.id,
        reservation_id=reservation_id,
        amount=amount,
        accepted_at=at,
    )
    session.add(bid)
    await session.flush()
    auction.highest_bid_id = bid.id
    auction.ends_at = extended_deadline(auction.ends_at, at)
    await session.execute(
        update(Job)
        .where(Job.dedupe_key == f"auction:v1:{auction.id}:close")
        .values(due_at=auction.ends_at, status="pending", last_error=None)
    )
    await notify(
        session, auction, "bid", auction.seller_ownership_id, auction.seller_club_id, at, bid
    )
    if old and old.buyer_club_id != club.id:
        await notify(session, auction, "outbid", old.buyer_ownership_id, old.buyer_club_id, at, old)
    await user_effect(
        session,
        replace(actor, at=at),
        "market_bid_accepted",
        Surface.CLUB,
        ownership=owner,
        club_id=club.id,
    )
    return BidResult(True)


class RosterRepairUnavailable(ValueError):
    pass


async def repair_lineup(
    session: AsyncSession,
    club_id: UUID,
    leaving: UUID | None,
    arriving: Player | None,
    at: datetime,
) -> None:
    lineup = await session.scalar(
        select(ClubLineup).where(ClubLineup.club_id == club_id).with_for_update()
    )
    if lineup is None:
        raise ValueError("Missing club lineup")
    slots = (
        await session.scalars(
            select(LineupSlot).where(LineupSlot.club_id == club_id).order_by(LineupSlot.slot)
        )
    ).all()
    starters = [(s.person_id, s.position) for s in slots if s.slot <= 11]
    bench = [(s.person_id, s.position) for s in slots if s.slot > 11 and s.person_id != leaving]
    if leaving in {pid for pid, _ in starters}:
        index = next(i for i, (pid, _) in enumerate(starters) if pid == leaving)
        position = starters[index][1]
        roster = (
            await session.scalars(
                select(Player)
                .where(
                    Player.club_id == club_id,
                    Player.person_id != leaving,
                    Player.status.in_(("active", "last_season")),
                )
                .order_by(Player.person_id)
            )
        ).all()
        selected = {pid for pid, _ in starters}
        candidates = [
            p
            for p in roster
            if p.person_id not in selected
            and (p.injured_until is None or p.injured_until <= at)
            and (p.position == "GK" if position == "GK" else p.position != "GK")
        ]
        candidates.sort(
            key=lambda p: (
                p.position != position,
                position not in p.alternative_positions,
                p.person_id not in {pid for pid, _ in bench},
                p.person_id,
            )
        )
        if not candidates:
            raise RosterRepairUnavailable("Sale leaves no eligible starter")
        replacement = candidates[0]
        starters[index] = (replacement.person_id, position)
        bench = [pair for pair in bench if pair[0] != replacement.person_id]
    if arriving and len(bench) < 9:
        bench.append((arriving.person_id, arriving.position))
    await session.execute(delete(LineupSlot).where(LineupSlot.club_id == club_id))
    await session.flush()
    session.add_all(
        [
            LineupSlot(club_id=club_id, slot=i, person_id=pid, position=pos)
            for i, (pid, pos) in enumerate((*starters, *bench), 1)
        ]
    )
    lineup.version += 1
    await session.flush()


async def close_auction(session: AsyncSession, job: Job, at: datetime) -> bool:
    """True means rescheduled, with accepted bids closed throughout the delay."""
    from goaletto.application.match_runtime import NotReady

    require_utc(at)
    if (
        job.kind != "market.close"
        or set(job.payload) != {"version", "auction_id"}
        or type(job.payload["version"]) is not int
        or job.payload["version"] != 1
    ):
        raise ValueError("Invalid auction job")
    if not await allocation_lock(session, try_lock=True):
        raise NotReady("Busy market")
    auction = await session.scalar(
        select(Auction)
        .where(Auction.id == UUID(job.payload["auction_id"]))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if auction is None or auction.policy_version != POLICY:
        raise ValueError("Missing auction or policy")
    if auction.status != "open":
        return False
    if at < auction.ends_at:
        job.due_at = auction.ends_at
        return True
    player, person = await player_locked(session, auction.person_id)
    why = await invalid_reason(session, auction, player, person, at)
    if why:
        await cancel_auction(session, auction, why, at)
        return False
    bid = await leading(session, auction)
    if bid is None:
        auction.status, auction.close_reason, auction.closed_at = "expired", "no_bids", at
        await notify(
            session, auction, "expired", auction.seller_ownership_id, auction.seller_club_id, at
        )
        return False
    buyer = await session.get(Club, bid.buyer_club_id)
    if buyer is None or not await owner_valid(
        session, bid.buyer_ownership_id, bid.buyer_club_id, at
    ):
        why = "ownership_changed"
    elif await league_set_for(session, buyer) != auction.league_set_id:
        why = "calendar_changed"
    elif await active_count(session, buyer.id) >= 25:
        why = "roster_changed"
    if why:
        await cancel_auction(session, auction, why, at)
        return False
    # A due settlement must not starve ticks while either club's match is active.
    busy = await session.scalar(
        select(Fixture.id)
        .where(
            (
                (Fixture.home_club_id.in_((auction.seller_club_id, bid.buyer_club_id)))
                | (Fixture.away_club_id.in_((auction.seller_club_id, bid.buyer_club_id)))
            ),
            Fixture.status.in_(("scheduled", "live", "paused")),
            Fixture.lineup_lock_at <= at,
            Fixture.actual_finish_at.is_(None),
        )
        .limit(1)
    )
    if busy:
        job.due_at = at + timedelta(seconds=60)
        return True
    accounts = await locked_accounts(session, auction.seller_club_id, bid.buyer_club_id)
    hold = await session.get(MoneyReservation, bid.reservation_id)
    if hold is None or hold.status != "active" or hold.amount != bid.amount:
        raise ValueError("Highest bid lacks a funded reservation")
    if accounts[auction.seller_club_id].club.budget > MAX_MONEY - bid.amount:
        await cancel_auction(session, auction, "funds_changed", at)
        return False
    # Validate roster repair before committing any transfer; full rollback on fault.
    try:
        await repair_lineup(session, auction.seller_club_id, player.person_id, None, at)
    except RosterRepairUnavailable:
        await cancel_auction(session, auction, "roster_changed", at)
        return False
    await close_salary(session, person.id, at)
    player.club_id = bid.buyer_club_id
    await session.flush()
    await repair_lineup(session, bid.buyer_club_id, None, player, at)
    await salary_contract(
        session, person.id, bid.buyer_club_id, "player", at, quoted_player_wage=auction.weekly_rate
    )
    await settle(session, bid.buyer_club_id, auction.seller_club_id, bid.reservation_id, at)
    session.add(
        TransferReceipt(
            id=uuid5(auction.id, "transfer"),
            auction_id=auction.id,
            person_id=person.id,
            seller_club_id=auction.seller_club_id,
            buyer_club_id=bid.buyer_club_id,
            price=bid.amount,
            transferred_at=at,
            snapshot={
                "person_name": person.name,
                "age": person.age,
                "age_applied_for_season_id": str(person.age_applied_for_season_id),
                "appearance_identity_id": person.appearance_identity_id,
                "appearance_variant_id": person.appearance_variant_id,
                "weekly_rate": auction.weekly_rate,
                "bid_id": str(bid.id),
                "policy": POLICY,
            },
        )
    )
    auction.status, auction.close_reason, auction.closed_at = "sold", "sold", at
    await notify(
        session, auction, "sold", auction.seller_ownership_id, auction.seller_club_id, at, bid
    )
    await notify(session, auction, "bought", bid.buyer_ownership_id, bid.buyer_club_id, at, bid)
    await session.flush()
    # The global market counts club auctions too. Only refill a remaining deficit.
    from goaletto.application.candidates import replenish_pool
    from goaletto.infrastructure.models import CandidatePool

    if await session.get(CandidatePool, ("player", "player")) is not None:
        await replenish_pool(session, "player", "player", at, limit=1)
    return False


async def withdraw_listing(
    session: AsyncSession, actor: Actor, ownership_id: UUID, auction_id: UUID
) -> Auction:
    owner, club = await owned_club(session, actor)
    auction = await session.scalar(
        select(Auction)
        .where(Auction.id == auction_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        auction is None
        or owner.id != ownership_id
        or auction.seller_ownership_id != owner.id
        or auction.seller_club_id != club.id
    ):
        raise PermissionDenied("Withdrawal requires seller owner")
    if auction.status != "open":
        return auction
    if auction.highest_bid_id is not None:
        raise ValueError("Cannot withdraw after a bid")
    await cancel_auction(session, auction, "withdrawn", actor.at)
    await user_effect(
        session, actor, "market_listing_cancelled", Surface.CLUB, ownership=owner, club_id=club.id
    )
    return auction


@dataclass(frozen=True)
class MarketItem:
    auction: Auction
    player: Player
    person: Person
    seller_name: str
    highest: AuctionBid | None


@dataclass(frozen=True)
class MarketScreen:
    club_name: str
    club_id: UUID
    ownership_id: UUID
    at: datetime
    window: MarketWindow
    cash: int
    reserved: int
    arrears: int
    roster_size: int
    incoming: int
    items: tuple[MarketItem, ...]
    page: int
    pages: int
    sell_candidates: tuple[tuple[Player, Person], ...]


async def item_for(session: AsyncSession, auction: Auction) -> MarketItem:
    player = await session.get(Player, auction.person_id)
    person = await session.get(Person, auction.person_id)
    seller = await session.get(Club, auction.seller_club_id)
    if player is None or person is None or seller is None:
        raise ValueError("Auction participants missing")
    return MarketItem(auction, player, person, seller.name, await leading(session, auction))


async def load_market(
    session: AsyncSession, actor: Actor, page: int = 0, *, record: bool = True
) -> MarketScreen:
    if type(page) is not int or not 0 <= page <= 999999:
        raise ValueError("Invalid market page")
    owner, club = await owned_club(session, actor)
    _, window = await calendar_for(session, club.id, actor.at)
    account = (await locked_accounts(session, club.id))[club.id]
    set_id = await league_set_for(session, club)
    count = (
        await session.scalar(
            select(func.count())
            .select_from(Auction)
            .where(Auction.league_set_id == set_id, Auction.status == "open")
        )
        or 0
    )
    pages = max(1, (count + 4) // 5)
    page = min(page, pages - 1)
    rows = (
        await session.scalars(
            select(Auction)
            .where(Auction.league_set_id == set_id, Auction.status == "open")
            .order_by(Auction.ends_at, Auction.id)
            .offset(page * 5)
            .limit(5)
        )
    ).all()
    candidates = (
        await session.execute(
            select(Player, Person)
            .join(Person, Person.id == Player.person_id)
            .where(
                Player.club_id == club.id,
                Player.status == "active",
                Person.career == "player",
                ~select(Auction.id)
                .where(Auction.person_id == Player.person_id, Auction.status == "open")
                .exists(),
            )
            .order_by(Person.name, Player.person_id)
            .limit(25)
        )
    ).all()
    if record:
        await user_effect(
            session, actor, "market_opened", Surface.CLUB, ownership=owner, club_id=club.id
        )
    return MarketScreen(
        club.name,
        club.id,
        owner.id,
        actor.at,
        window,
        club.budget,
        account.record.reserved,
        account.record.arrears,
        await active_count(session, club.id),
        await incoming_count(session, club.id),
        tuple([await item_for(session, row) for row in rows]),
        page,
        pages,
        tuple((p, n) for p, n in candidates),
    )


async def get_auction(
    session: AsyncSession, actor: Actor, auction_id: UUID, *, record: bool = True
) -> tuple[MarketScreen, MarketItem]:
    screen = await load_market(session, actor, record=record)
    auction = await session.get(Auction, auction_id)
    club = await session.get(Club, screen.club_id)
    if (
        auction is None
        or club is None
        or auction.league_set_id != await league_set_for(session, club)
    ):
        raise PermissionDenied("Auction belongs to a different calendar")
    return screen, await item_for(session, auction)
