"""PostgreSQL regression races missing from earlier gather tests.

Real transactions on the single development DB with separate AsyncSession
connections and the application's own locking. Order is synchronized with
asyncio.Event and controlled capture of real PG locks (advisory try-lock or
FOR UPDATE NOWAIT). Arbitrary sleep never proves the race.

PG execution is done by Codex after backup; this change only adds cases.
"""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from goaletto.application.game_actions import Actor, allocation_lock
from goaletto.application.market import (
    close_auction,
    list_player,
    place_bid,
    player_locked,
    prohibit_player_trades,
)
from goaletto.application.match_runtime import NotReady
from goaletto.application.money import credit_locked, locked_accounts
from goaletto.application.onboarding import begin_game, claim_club
from goaletto.application.outbox_delivery import (
    LeaseLost,
    Outcome,
    SendResult,
    claim_outbox,
    finish_delivery,
    preflight_delivery,
)
from goaletto.infrastructure.models import (
    Auction,
    AuctionBid,
    Club,
    FinanceAccount,
    Job,
    LedgerEntry,
    LineupSlot,
    MoneyReservation,
    OutboxBoard,
    OutboxDeliveryAttempt,
    OutboxMessage,
    OwnershipPeriod,
    Player,
    TransferReceipt,
)
from tests.integration.race_helpers import (
    active_reservation_total,
    allocation_held_by_other,
    auction_row_locked_elsewhere,
    gather_bounded,
    player_row_locked_elsewhere,
)
from tests.integration.test_agent_wave import NOW, setup_world, start_fixture
from tests.integration.test_agent_wave import job as advance_job

pytestmark = pytest.mark.integration

_TIMEOUT = 15.0


def fresh_action(actor: Actor, at: datetime = NOW) -> Actor:
    return replace(actor, operation_id=uuid4(), update_id=uuid4().int % 1000000000, at=at)


async def market_race_world(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[list[tuple[Actor, UUID, UUID]], UUID, UUID, UUID]:
    """Three funded clubs and one open seller auction; caller owns no transaction."""
    _, seller_actor, seller_club = await setup_world(sessions)
    pairs: list[tuple[Actor, UUID]] = [(seller_actor, seller_club)]
    async with sessions.begin() as session:
        for telegram_id in (1002, 1003):
            entry = await begin_game(
                session, telegram_id=telegram_id, update_id=telegram_id, payload=None, at=NOW
            )
            actor = fresh_action(entry.actor)
            owner = await claim_club(session, actor, entry.offers[0].id)
            pairs.append((actor, owner.club_id))
        funded: list[tuple[Actor, UUID, UUID]] = []
        for actor, club_id in pairs:
            account = (await locked_accounts(session, club_id))[club_id]
            await credit_locked(session, account, f"test-race:{club_id}", "grant", 1000000, NOW)
            owner_id = await session.scalar(
                select(OwnershipPeriod.id).where(
                    OwnershipPeriod.club_id == club_id, OwnershipPeriod.ended_at.is_(None)
                )
            )
            assert owner_id is not None
            funded.append((actor, club_id, owner_id))
        player = await session.scalar(
            select(Player)
            .join(LineupSlot, LineupSlot.person_id == Player.person_id)
            .where(
                Player.club_id == seller_club,
                Player.status == "active",
                LineupSlot.slot.between(2, 11),
            )
            .order_by(LineupSlot.slot)
            .limit(1)
        )
        assert player is not None
        auction = await list_player(
            session,
            fresh_action(seller_actor),
            funded[0][2],
            player.person_id,
            100000,
            clock=lambda: NOW,
        )
        auction_id: UUID = auction.id
        person_id: UUID = player.person_id
        season_id: UUID = auction.season_id
    return funded, auction_id, person_id, season_id


async def close_job_for(session: AsyncSession, auction_id: UUID) -> Job:
    job = await session.scalar(
        select(Job).where(
            Job.kind == "market.close", Job.payload["auction_id"].astext == str(auction_id)
        )
    )
    assert job is not None
    return job


async def test_bid_vs_prohibit_early_bid_then_cancelled_refunded_once(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Bid wins the allocation lock, then the announcement cancels and refunds once."""
    people, auction_id, person_id, season_id = await market_race_world(sessions)
    buyer_actor, buyer_club, buyer_owner = people[1]
    seller_club = people[0][1]
    async with sessions.begin() as session:
        buyer_row = await session.get(Club, buyer_club)
        seller_row = await session.get(Club, seller_club)
        assert buyer_row is not None and seller_row is not None
        buyer_before = buyer_row.budget
        seller_before = seller_row.budget

    bid_acquired = asyncio.Event()
    retire_started = asyncio.Event()

    async def bid_task() -> bool:
        async with sessions.begin() as session:
            await allocation_lock(session)
            held = await session.scalar(
                select(Auction).where(Auction.id == auction_id).with_for_update()
            )
            assert held is not None and held.status == "open"
            bid_acquired.set()
            await asyncio.wait_for(retire_started.wait(), 10)
            result = await place_bid(
                session,
                fresh_action(buyer_actor),
                buyer_owner,
                auction_id,
                100000,
                clock=lambda: NOW,
            )
            assert result.accepted
        return True

    async def retire_task() -> bool:
        await asyncio.wait_for(bid_acquired.wait(), 10)
        assert await auction_row_locked_elsewhere(sessions, auction_id)
        retire_started.set()
        async with sessions.begin() as session:
            # Blocks on the allocation advisory lock held by the bidder.
            await allocation_lock(session)
            locked_player, _ = await player_locked(session, person_id)
            assert locked_player.club_id == seller_club
            locked_player.status = "last_season"
            locked_player.last_season_id = season_id
            await session.flush()
            assert await prohibit_player_trades(session, person_id, NOW) == 1
        return True

    await gather_bounded(bid_task(), retire_task(), bound=_TIMEOUT)

    async with sessions.begin() as session:
        auction = await session.get(Auction, auction_id)
        assert auction is not None
        assert auction.status == "cancelled" and auction.close_reason == "last_season"
        player = await session.get(Player, person_id)
        assert player is not None
        assert player.club_id == seller_club and player.status == "last_season"
        assert await active_reservation_total(session, buyer_club) == 0
        buyer_account = await session.get(FinanceAccount, buyer_club)
        assert buyer_account is not None and buyer_account.reserved == 0
        bid = await session.scalar(select(AuctionBid).where(AuctionBid.auction_id == auction_id))
        assert bid is not None
        hold = await session.get(MoneyReservation, bid.reservation_id)
        assert hold is not None and hold.status == "released"
        releases = (
            await session.scalars(
                select(LedgerEntry).where(
                    LedgerEntry.club_id == buyer_club,
                    LedgerEntry.operation_key == f"release:{hold.id}",
                )
            )
        ).all()
        assert len(releases) == 1
        settles = (
            await session.scalars(
                select(LedgerEntry).where(
                    LedgerEntry.club_id == buyer_club,
                    LedgerEntry.operation_key == f"settle:{hold.id}",
                )
            )
        ).all()
        assert len(settles) == 0
        buyer_final = await session.get(Club, buyer_club)
        seller_final = await session.get(Club, seller_club)
        assert buyer_final is not None and buyer_final.budget == buyer_before
        assert seller_final is not None and seller_final.budget == seller_before
        close_job = await close_job_for(session, auction_id)
        assert close_job.status == "done"
        cancel_job = await session.scalar(select(Job).where(Job.kind == "market.cancel_player"))
        assert cancel_job is not None
        assert await session.scalar(select(func.count()).select_from(TransferReceipt)) == 0
        assert await prohibit_player_trades(session, person_id, NOW) == 0


async def test_bid_vs_prohibit_late_bid_refused_no_reserve(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Announcement wins; a late bid raises without creating a reserve."""
    people, auction_id, person_id, season_id = await market_race_world(sessions)
    buyer_actor, buyer_club, buyer_owner = people[1]
    seller_club = people[0][1]

    retire_acquired = asyncio.Event()
    bid_started = asyncio.Event()

    async def retire_task() -> bool:
        async with sessions.begin() as session:
            await allocation_lock(session)
            locked_player, _ = await player_locked(session, person_id)
            assert locked_player.club_id == seller_club
            retire_acquired.set()
            await asyncio.wait_for(bid_started.wait(), 10)
            locked_player.status = "last_season"
            locked_player.last_season_id = season_id
            await session.flush()
            assert await prohibit_player_trades(session, person_id, NOW) == 1
        return True

    async def bid_task() -> bool:
        await asyncio.wait_for(retire_acquired.wait(), 10)
        assert await player_row_locked_elsewhere(sessions, person_id)
        bid_started.set()
        async with sessions.begin() as session:
            with pytest.raises(ValueError, match="Auction closed"):
                await place_bid(
                    session,
                    fresh_action(buyer_actor),
                    buyer_owner,
                    auction_id,
                    100000,
                    clock=lambda: NOW,
                )
        return True

    await gather_bounded(retire_task(), bid_task(), bound=_TIMEOUT)

    async with sessions.begin() as session:
        auction = await session.get(Auction, auction_id)
        assert auction is not None
        assert auction.status == "cancelled" and auction.close_reason == "last_season"
        assert await session.scalar(select(func.count()).select_from(AuctionBid)) == 0
        assert await active_reservation_total(session, buyer_club) == 0
        buyer_account = await session.get(FinanceAccount, buyer_club)
        assert buyer_account is not None and buyer_account.reserved == 0
        assert await session.scalar(select(func.count()).select_from(TransferReceipt)) == 0
        player = await session.get(Player, person_id)
        assert player is not None and player.club_id == seller_club


async def test_settlement_vs_prohibit_announce_first_blocks_sale(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """With a leading bid, the announcement commits first and the close never sells."""
    people, auction_id, person_id, season_id = await market_race_world(sessions)
    buyer_actor, buyer_club, buyer_owner = people[1]
    seller_club = people[0][1]
    async with sessions.begin() as session:
        await place_bid(
            session,
            fresh_action(buyer_actor),
            buyer_owner,
            auction_id,
            100000,
            clock=lambda: NOW,
        )
        auction_row = await session.get(Auction, auction_id)
        buyer_row = await session.get(Club, buyer_club)
        seller_row = await session.get(Club, seller_club)
        assert auction_row is not None and buyer_row is not None and seller_row is not None
        due = auction_row.ends_at
        buyer_before = buyer_row.budget
        seller_before = seller_row.budget

    retire_acquired = asyncio.Event()
    close_started = asyncio.Event()
    retire_done = asyncio.Event()

    async def retire_task() -> bool:
        async with sessions.begin() as session:
            await allocation_lock(session)
            locked_player, _ = await player_locked(session, person_id)
            assert locked_player.club_id == seller_club
            retire_acquired.set()
            await asyncio.wait_for(close_started.wait(), 10)
            locked_player.status = "last_season"
            locked_player.last_season_id = season_id
            await session.flush()
            assert await prohibit_player_trades(session, person_id, due) == 1
        retire_done.set()
        return True

    async def close_task() -> bool:
        await asyncio.wait_for(retire_acquired.wait(), 10)
        assert await allocation_held_by_other(sessions)
        close_started.set()
        async with sessions.begin() as session:
            job = await close_job_for(session, auction_id)
            try:
                await close_auction(session, job, due)
            except NotReady:
                pass
            else:  # pragma: no cover - allocation held, so try-lock must defer
                raise AssertionError("close must defer while announcement holds the lock")
        await asyncio.wait_for(retire_done.wait(), 10)
        async with sessions.begin() as session:
            job = await close_job_for(session, auction_id)
            assert not await close_auction(session, job, due)
            job.status = "done"
        return True

    await gather_bounded(retire_task(), close_task(), bound=_TIMEOUT)

    async with sessions.begin() as session:
        auction = await session.get(Auction, auction_id)
        assert auction is not None
        assert auction.status == "cancelled" and auction.close_reason == "last_season"
        player = await session.get(Player, person_id)
        assert player is not None and player.club_id == seller_club
        assert await active_reservation_total(session, buyer_club) == 0
        bid = await session.scalar(select(AuctionBid).where(AuctionBid.auction_id == auction_id))
        assert bid is not None
        hold = await session.get(MoneyReservation, bid.reservation_id)
        assert hold is not None and hold.status == "released"
        buyer_final = await session.get(Club, buyer_club)
        seller_final = await session.get(Club, seller_club)
        assert buyer_final is not None and buyer_final.budget == buyer_before
        assert seller_final is not None and seller_final.budget == seller_before
        assert await session.scalar(select(func.count()).select_from(TransferReceipt)) == 0


async def test_settlement_vs_prohibit_sale_first_moves_flag_follows_buyer(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Sale commits first; the later announcement flags the buyer's roster, not the seller."""
    people, auction_id, person_id, season_id = await market_race_world(sessions)
    buyer_actor, buyer_club, buyer_owner = people[1]
    seller_club = people[0][1]
    async with sessions.begin() as session:
        await place_bid(
            session,
            fresh_action(buyer_actor),
            buyer_owner,
            auction_id,
            100000,
            clock=lambda: NOW,
        )
        auction_row = await session.get(Auction, auction_id)
        assert auction_row is not None
        due = auction_row.ends_at

    close_acquired = asyncio.Event()
    retire_started = asyncio.Event()

    async def close_task() -> bool:
        async with sessions.begin() as session:
            await allocation_lock(session)
            held = await session.scalar(
                select(Auction).where(Auction.id == auction_id).with_for_update()
            )
            assert held is not None and held.status == "open"
            close_acquired.set()
            await asyncio.wait_for(retire_started.wait(), 10)
            job = await close_job_for(session, auction_id)
            assert not await close_auction(session, job, due)
            job.status = "done"
        return True

    async def retire_task() -> bool:
        await asyncio.wait_for(close_acquired.wait(), 10)
        assert await auction_row_locked_elsewhere(sessions, auction_id)
        retire_started.set()
        async with sessions.begin() as session:
            await allocation_lock(session)
            locked_player, _ = await player_locked(session, person_id)
            # Sale already moved the footballer before this lock was granted.
            assert locked_player.club_id == buyer_club
            locked_player.status = "last_season"
            locked_player.last_season_id = season_id
            await session.flush()
            # Sold auctions are never reopened or cancelled.
            assert await prohibit_player_trades(session, person_id, due) == 0
        return True

    await gather_bounded(close_task(), retire_task(), bound=_TIMEOUT)

    async with sessions.begin() as session:
        auction = await session.get(Auction, auction_id)
        assert auction is not None
        assert auction.status == "sold" and auction.close_reason == "sold"
        player = await session.get(Player, person_id)
        assert player is not None
        assert player.club_id == buyer_club and player.status == "last_season"
        assert await session.scalar(select(func.count()).select_from(TransferReceipt)) == 1
        assert await active_reservation_total(session, buyer_club) == 0
        bid = await session.scalar(select(AuctionBid).where(AuctionBid.auction_id == auction_id))
        assert bid is not None
        hold = await session.get(MoneyReservation, bid.reservation_id)
        assert hold is not None and hold.status == "settled"
        seller_final = await session.get(Club, seller_club)
        buyer_final = await session.get(Club, buyer_club)
        assert seller_final is not None and seller_final.budget == 1150000
        assert buyer_final is not None and buyer_final.budget == 950000


async def test_late_bid_extension_vs_taken_close_job_defers_with_single_reserve(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Late bid extends while the close job waits; no early transfer, future job kept."""
    people, auction_id, _, _ = await market_race_world(sessions)
    buyer_actor, buyer_club, buyer_owner = people[1]
    seller_club = people[0][1]
    late_at = NOW + timedelta(hours=47)
    async with sessions.begin() as session:
        auction_row = await session.get(Auction, auction_id)
        assert auction_row is not None
        old_ends = auction_row.ends_at
    assert old_ends == NOW + timedelta(hours=48)
    new_ends = old_ends + timedelta(minutes=30)

    bid_acquired = asyncio.Event()
    close_started = asyncio.Event()

    async def bid_task() -> bool:
        async with sessions.begin() as session:
            await allocation_lock(session)
            held = await session.scalar(
                select(Auction).where(Auction.id == auction_id).with_for_update()
            )
            assert held is not None and held.status == "open"
            bid_acquired.set()
            await asyncio.wait_for(close_started.wait(), 10)
            result = await place_bid(
                session,
                fresh_action(buyer_actor, late_at),
                buyer_owner,
                auction_id,
                100000,
                clock=lambda: late_at,
            )
            assert result.accepted
            bid_auction = await session.get(Auction, auction_id)
            assert bid_auction is not None and bid_auction.ends_at == new_ends
        return True

    async def close_task() -> bool:
        await asyncio.wait_for(bid_acquired.wait(), 10)
        assert await auction_row_locked_elsewhere(sessions, auction_id)
        close_started.set()
        async with sessions.begin() as session:
            job = await close_job_for(session, auction_id)
            try:
                rescheduled = await close_auction(session, job, old_ends)
            except NotReady:
                rescheduled = True
            assert rescheduled
        async with sessions.begin() as session:
            job = await close_job_for(session, auction_id)
            # After the bid commits the close must observe the extended deadline.
            assert await close_auction(session, job, old_ends)
            assert job.due_at == new_ends
            job.status = "pending"
        return True

    await gather_bounded(bid_task(), close_task(), bound=_TIMEOUT)

    async with sessions.begin() as session:
        auction = await session.get(Auction, auction_id)
        assert auction is not None
        assert auction.status == "open" and auction.ends_at == new_ends
        owner_player = await session.get(Player, auction.person_id)
        assert owner_player is not None and owner_player.club_id == seller_club
        assert await active_reservation_total(session, buyer_club) == 100000
        buyer_account = await session.get(FinanceAccount, buyer_club)
        assert buyer_account is not None and buyer_account.reserved == 100000
        bids = (
            await session.scalars(select(AuctionBid).where(AuctionBid.auction_id == auction_id))
        ).all()
        assert len(bids) == 1
        reserves = (
            await session.scalars(
                select(LedgerEntry).where(
                    LedgerEntry.club_id == buyer_club, LedgerEntry.kind == "reserve"
                )
            )
        ).all()
        assert len(reserves) == 1 and reserves[0].cash_delta == 0
        assert await session.scalar(select(func.count()).select_from(TransferReceipt)) == 0
        job = await close_job_for(session, auction_id)
        assert job.status == "pending" and job.due_at == new_ends

    with pytest.raises(RuntimeError, match="Abort deferred close"):
        async with sessions.begin() as session:
            job = await close_job_for(session, auction_id)
            assert await close_auction(session, job, old_ends)
            raise RuntimeError("Abort deferred close")
    async with sessions.begin() as session:
        auction = await session.get(Auction, auction_id)
        assert auction is not None and auction.status == "open" and auction.ends_at == new_ends
        assert await active_reservation_total(session, buyer_club) == 100000
        assert (await close_job_for(session, auction_id)).due_at == new_ends


class _FakeTransport:
    """Telegram transport without a DB transaction; network runs after commit."""

    def __init__(self, message_id: int) -> None:
        self.message_id = message_id
        self.calls = 0

    async def send_text(self, chat_id: int, text: str) -> SendResult:
        assert isinstance(chat_id, int) and isinstance(text, str)
        self.calls += 1
        return SendResult(self.message_id)

    async def edit_text(self, chat_id: int, message_id: int, text: str) -> None:
        raise AssertionError("board test uses send for the first revision")


async def test_sender_lease_takeover_old_finish_preserves_unknown(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    """Expired sender loses the fence; unknown survives, old finish never overwrites."""
    _, _, club_id = await setup_world(sessions)
    fixture_id, kickoff_at = await start_fixture(sessions, club_id)
    await advance_job(sessions, "match.tick", fixture_id, 1)
    t0 = kickoff_at + timedelta(seconds=61)
    t1 = t0 + timedelta(seconds=121)

    async with sessions.begin() as session:
        claimed = await claim_outbox(session, t0, lease_owner="sender-a-old")
        assert len(claimed) == 1
        row_id = claimed[0].id
        attempt_one = claimed[0].attempts
        board_key = claimed[0].live_board_key
        assert board_key is not None
    async with sessions.begin() as session:
        ready = await preflight_delivery(
            session, row_id, t0, lease_owner="sender-a-old", attempt=attempt_one
        )
        assert ready is not None
    # Network succeeds outside any DB transaction, then the process crashes
    # before finish. The durable attempt stays at network-started/sending.
    # The 999 id is never committed; it only proves the old sender cannot
    # overwrite the new claim below. Exactly-once Telegram send is not claimed.
    old_transport = _FakeTransport(999)
    old_result = await old_transport.send_text(1, "board")
    assert old_result.message_id == 999

    async with sessions.begin() as session:
        # Lease expired: a new sender takes the stream and marks the old sending unknown.
        retaken = await claim_outbox(session, t1, lease_owner="sender-b-new")
        assert len(retaken) == 1 and retaken[0].id == row_id
        assert retaken[0].attempts == attempt_one + 1
        assert retaken[0].lease_owner == "sender-b-new"
        first = await session.get(OutboxDeliveryAttempt, (row_id, attempt_one))
        assert first is not None
        assert first.outcome == "unknown" and first.finished_at == t1
        second = await session.get(OutboxDeliveryAttempt, (row_id, attempt_one + 1))
        assert second is not None and second.outcome == "claimed"
        board = await session.get(OutboxBoard, board_key)
        assert board is not None
        assert board.lease_owner == "sender-b-new" and board.telegram_message_id is None
        message = await session.get(OutboxMessage, row_id)
        assert message is not None
        assert message.status == "pending" and message.telegram_message_id is None

    # Late finish from the expired sender must not overwrite the new claim.
    async with sessions.begin() as session:
        with pytest.raises(LeaseLost):
            await finish_delivery(
                session,
                row_id,
                t1,
                Outcome.SENT,
                lease_owner="sender-a-old",
                attempt=attempt_one,
                telegram_message_id=999,
            )
    async with sessions.begin() as session:
        message = await session.get(OutboxMessage, row_id)
        assert message is not None
        assert message.status == "pending" and message.telegram_message_id is None
        assert message.lease_owner == "sender-b-new" and message.attempts == attempt_one + 1
        board = await session.get(OutboxBoard, board_key)
        assert board is not None and board.telegram_message_id is None
        assert board.lease_owner == "sender-b-new"

    # The new owner completes honestly; the old unknown is preserved, never sent.
    new_transport = _FakeTransport(777)
    await new_transport.send_text(1, "board")
    async with sessions.begin() as session:
        ready = await preflight_delivery(
            session, row_id, t1, lease_owner="sender-b-new", attempt=attempt_one + 1
        )
        assert ready is not None
    async with sessions.begin() as session:
        finished = await finish_delivery(
            session,
            row_id,
            t1,
            Outcome.SENT,
            lease_owner="sender-b-new",
            attempt=attempt_one + 1,
            telegram_message_id=777,
        )
        assert finished.status == "sent" and finished.telegram_message_id == 777
    async with sessions.begin() as session:
        first = await session.get(OutboxDeliveryAttempt, (row_id, attempt_one))
        second = await session.get(OutboxDeliveryAttempt, (row_id, attempt_one + 1))
        assert first is not None and first.outcome == "unknown"
        assert second is not None and second.outcome == "sent"
        board = await session.get(OutboxBoard, board_key)
        assert board is not None and board.telegram_message_id == 777
        # No exactly-once Telegram claim: transport 999 may have delivered
        # before the crash, hence a duplicate on retry stays possible.
