"""Auctions on the single development DB: races, settlement, locks and refunds."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.game_actions import end_expired
from goaletto.application.market import (
    close_auction,
    list_player,
    load_market,
    place_bid,
    prohibit_player_trades,
    withdraw_listing,
)
from goaletto.application.money import InsufficientFunds, credit_locked, locked_accounts
from goaletto.application.onboarding import begin_game, claim_club
from goaletto.infrastructure.models import (
    Auction,
    AuctionBid,
    Club,
    FinanceAccount,
    Fixture,
    Job,
    LineupSlot,
    MoneyReservation,
    OwnershipPeriod,
    Person,
    Player,
    SalaryContract,
    TransferReceipt,
)
from tests.integration.test_agent_wave import NOW, setup_world

pytestmark = pytest.mark.integration


def action(actor, at=NOW):
    return replace(actor, operation_id=uuid4(), update_id=uuid4().int % 1000000000, at=at)


async def market_world(sessions):
    _, seller, seller_id = await setup_world(sessions)
    actors = [(seller, seller_id)]
    async with sessions.begin() as session:
        for telegram_id in (1002, 1003):
            entry = await begin_game(
                session, telegram_id=telegram_id, update_id=telegram_id, payload=None, at=NOW
            )
            actor = action(entry.actor)
            owner = await claim_club(session, actor, entry.offers[0].id)
            actors.append((actor, owner.club_id))
        output = []
        for actor, club_id in actors:
            account = (await locked_accounts(session, club_id))[club_id]
            await credit_locked(session, account, f"test-market:{club_id}", "grant", 1000000, NOW)
            owner_id = await session.scalar(
                select(OwnershipPeriod.id).where(
                    OwnershipPeriod.club_id == club_id, OwnershipPeriod.ended_at.is_(None)
                )
            )
            output.append((actor, club_id, owner_id))
        player = await session.scalar(
            select(Player)
            .join(LineupSlot, LineupSlot.person_id == Player.person_id)
            .where(
                Player.club_id == seller_id,
                Player.status == "active",
                LineupSlot.slot.between(2, 11),
            )
            .order_by(LineupSlot.slot)
            .limit(1)
        )
        auction = await list_player(
            session, action(seller), output[0][2], player.person_id, 100000, clock=lambda: NOW
        )
        auction_id, person_id = auction.id, player.person_id
    return output, auction_id, person_id


async def close(sessions, auction_id, at=None):
    async with sessions.begin() as session:
        work = await session.scalar(
            select(Job).where(
                Job.kind == "market.close", Job.payload["auction_id"].astext == str(auction_id)
            )
        )
        rescheduled = await close_auction(session, work, at or work.due_at)
        if not rescheduled:
            work.status = "done"
        return rescheduled


async def test_parallel_bids_outbid_self_raise_retry_and_atomic_transfer(sessions):
    people, auction_id, person_id = await market_world(sessions)
    buyer_actors = [action(p[0]) for p in people[1:]]

    async def bid(i):
        try:
            async with sessions.begin() as session:
                return (
                    await place_bid(
                        session,
                        buyer_actors[i],
                        people[i + 1][2],
                        auction_id,
                        100000,
                        clock=lambda: NOW,
                    )
                ).accepted
        except ValueError:
            return False

    values = await asyncio.wait_for(asyncio.gather(bid(0), bid(1)), 10)
    assert sum(values) == 1
    winning = values.index(True) + 1
    other = 3 - winning
    async with sessions.begin() as session:
        screen = await load_market(session, action(people[other][0]))
        assert screen.items[0].auction.id == auction_id
        person = await session.get(Person, person_id)
        identity = (
            person.age,
            person.appearance_identity_id,
            person.appearance_variant_id,
            person.age_applied_for_season_id,
        )
        oldbid = await session.scalar(select(AuctionBid).where(AuctionBid.auction_id == auction_id))
        await place_bid(
            session,
            action(people[other][0]),
            people[other][2],
            auction_id,
            105000,
            clock=lambda: NOW,
        )
        assert (await session.get(FinanceAccount, people[winning][1])).reserved == 0
        await place_bid(
            session,
            buyer_actors[winning - 1],
            people[winning][2],
            auction_id,
            100000,
            clock=lambda: NOW,
        )
        assert (await session.get(MoneyReservation, oldbid.reservation_id)).status == "released"
        assert (await session.get(FinanceAccount, people[winning][1])).reserved == 0
        again = action(people[other][0])
        await place_bid(session, again, people[other][2], auction_id, 110000, clock=lambda: NOW)
        await place_bid(session, again, people[other][2], auction_id, 110000, clock=lambda: NOW)
        assert (await session.get(FinanceAccount, people[other][1])).reserved == 110000
        with pytest.raises(ValueError):
            await place_bid(session, again, people[other][2], auction_id, 115000, clock=lambda: NOW)
        player = await session.get(Player, person_id)
        player.playmaking += 1
        quoted = (await session.get(Auction, auction_id)).weekly_rate
    assert not await close(sessions, auction_id)
    assert not await close(sessions, auction_id)
    async with sessions.begin() as session:
        assert (await session.get(Auction, auction_id)).status == "sold"
        assert (await session.get(Player, person_id)).club_id == people[other][1]
        person = await session.get(Person, person_id)
        assert (
            person.age,
            person.appearance_identity_id,
            person.appearance_variant_id,
            person.age_applied_for_season_id,
        ) == identity
        assert (await session.get(Club, people[0][1])).budget == 1160000
        assert (await session.get(Club, people[other][1])).budget == 940000
        assert (await session.get(FinanceAccount, people[other][1])).reserved == 0
        assert await session.scalar(select(func.count()).select_from(TransferReceipt)) == 1
        contract = await session.scalar(
            select(SalaryContract).where(
                SalaryContract.person_id == person_id, SalaryContract.ended_at.is_(None)
            )
        )
        assert contract.weekly_rate == quoted and contract.club_id == people[other][1]
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LineupSlot)
                .where(LineupSlot.club_id == people[0][1])
            )
            == 19
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LineupSlot)
                .where(LineupSlot.club_id == people[other][1])
            )
            == 20
        )
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(text("UPDATE auction_bids SET amount=amount+5000"))


async def test_last_season_flag_queues_cancellation_refunds_once_and_blocks_listing(sessions):
    people, auction_id, person_id = await market_world(sessions)
    buyer, _, buyer_owner = people[1]
    async with sessions.begin() as session:
        await place_bid(session, action(buyer), buyer_owner, auction_id, 100000, clock=lambda: NOW)
        auction = await session.get(Auction, auction_id)
        player = await session.get(Player, person_id)
        player.status = "last_season"
        player.last_season_id = auction.season_id
        await session.flush()
        assert await session.scalar(select(Job.id).where(Job.kind == "market.cancel_player"))
        assert await prohibit_player_trades(session, person_id, NOW) == 1
        assert await prohibit_player_trades(session, person_id, NOW) == 0
        assert (await session.get(FinanceAccount, people[1][1])).reserved == 0
        assert auction.status == "cancelled" and auction.close_reason == "last_season"
        with pytest.raises(ValueError):
            await list_player(
                session, action(people[0][0]), people[0][2], person_id, 100000, clock=lambda: NOW
            )
    assert not await close(sessions, auction_id)
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(TransferReceipt)) == 0
        assert (await session.get(Player, person_id)).club_id == people[0][1]


async def test_late_bid_extends_once_insufficient_money_withdrawal_and_lease_expiry(sessions):
    people, auction_id, _ = await market_world(sessions)
    at = NOW + timedelta(hours=47)
    bidder = action(people[1][0], at)
    async with sessions.begin() as session:
        with pytest.raises(InsufficientFunds):
            await place_bid(
                session, action(bidder, at), people[1][2], auction_id, 2000000, clock=lambda: at
            )
        await place_bid(session, bidder, people[1][2], auction_id, 100000, clock=lambda: at)
        await place_bid(session, bidder, people[1][2], auction_id, 100000, clock=lambda: at)
        auction = await session.get(Auction, auction_id)
        assert auction.ends_at == NOW + timedelta(hours=48, minutes=30)
        with pytest.raises(ValueError):
            await withdraw_listing(session, action(people[0][0]), people[0][2], auction_id)
        owner = await session.get(OwnershipPeriod, people[1][2])
        assert await end_expired(session, owner, owner.expires_at)
        assert auction.status == "cancelled"
        assert (await session.get(FinanceAccount, people[1][1])).reserved == 0


async def test_busy_match_defers_settlement_and_fault_rolls_back_entire_trade(sessions):
    people, auction_id, person_id = await market_world(sessions)
    async with sessions.begin() as session:
        await place_bid(
            session, action(people[1][0]), people[1][2], auction_id, 100000, clock=lambda: NOW
        )
        auction = await session.get(Auction, auction_id)
        due = auction.ends_at
        fixture = await session.scalar(
            select(Fixture).where(Fixture.home_club_id == people[0][1]).limit(1)
        )
        fixture.lineup_lock_at = due - timedelta(minutes=5)
        fixture.scheduled_start_at = due
        fixture_id = fixture.id
    assert await close(sessions, auction_id, due)
    async with sessions.begin() as session:
        fixture = await session.get(Fixture, fixture_id)
        fixture.status = "finished"
        fixture.actual_start_at = due
        fixture.actual_finish_at = due + timedelta(seconds=30)
    with pytest.raises(RuntimeError):
        async with sessions.begin() as session:
            work = await session.scalar(select(Job).where(Job.kind == "market.close"))
            assert not await close_auction(session, work, due + timedelta(seconds=60))
            raise RuntimeError("Abort settlement")
    async with sessions.begin() as session:
        assert (await session.get(Player, person_id)).club_id == people[0][1]
        assert (await session.get(Auction, auction_id)).status == "open"
        assert (await session.get(FinanceAccount, people[1][1])).reserved == 100000
        assert await session.scalar(select(func.count()).select_from(TransferReceipt)) == 0
    assert not await close(sessions, auction_id, due + timedelta(seconds=60))


async def test_roster_limits_window_guard_and_expiration_without_bids(sessions):
    people, auction_id, _ = await market_world(sessions)
    async with sessions.begin() as session:
        seller, club_id, owner_id = people[0]
        eligible = (
            await session.scalars(
                select(Player)
                .where(
                    Player.club_id == club_id,
                    Player.status == "active",
                    Player.position != "GK",
                    Player.person_id != (await session.get(Auction, auction_id)).person_id,
                )
                .limit(4)
            )
        ).all()
        for player in eligible[:3]:
            await list_player(
                session, action(seller), owner_id, player.person_id, 100000, clock=lambda: NOW
            )
        with pytest.raises(ValueError):
            await list_player(
                session, action(seller), owner_id, eligible[3].person_id, 100000, clock=lambda: NOW
            )
        with pytest.raises(ValueError):
            await place_bid(
                session, action(seller), owner_id, auction_id, 100000, clock=lambda: NOW
            )
        at = NOW + timedelta(days=8)
        with pytest.raises(ValueError):
            await list_player(
                session,
                action(seller, at),
                owner_id,
                eligible[3].person_id,
                100000,
                clock=lambda: at,
            )
    assert not await close(sessions, auction_id)
    async with sessions.begin() as session:
        assert (await session.get(Auction, auction_id)).status == "expired"
        assert await session.scalar(select(func.count()).select_from(AuctionBid)) == 0


async def test_outbox_guards_latest_bid_and_notice_after_transfer(sessions):
    from goaletto.application.outbox_delivery import Decision, check_recipient, valid_payload
    from goaletto.infrastructure.models import OutboxMessage

    people, auction_id, _ = await market_world(sessions)
    async with sessions.begin() as session:
        for i, amount in ((1, 100000), (2, 105000), (1, 110000)):
            await place_bid(
                session, action(people[i][0]), people[i][2], auction_id, amount, clock=lambda: NOW
            )
        messages = (
            await session.scalars(
                select(OutboxMessage).where(OutboxMessage.payload["kind"].astext == "market_notice")
            )
        ).all()
        assert all(valid_payload(m.payload) for m in messages)
        regained = next(
            m for m in messages if m.payload["event"] == "outbid" and m.payload["amount"] == 100000
        )
        assert await check_recipient(session, regained, NOW) == Decision.SKIP_STALE
        newest = next(
            m for m in messages if m.payload["event"] == "bid" and m.payload["amount"] == 110000
        )
        assert await check_recipient(session, newest, NOW) == Decision.DELIVER
    await close(sessions, auction_id)
    async with sessions.begin() as session:
        messages = (
            await session.scalars(
                select(OutboxMessage).where(
                    OutboxMessage.payload["event"].astext.in_(("sold", "bought"))
                )
            )
        ).all()
        assert len(messages) == 2
        assert all(valid_payload(m.payload) for m in messages)
        for m in messages:
            assert await check_recipient(session, m, NOW + timedelta(days=2)) == Decision.DELIVER


async def test_pending_purchases_reserve_roster_slots_and_parallel_close_is_once(sessions):
    people, first_id, _ = await market_world(sessions)
    auctions = [first_id]
    async with sessions.begin() as session:
        for seller_index, needed in ((0, 3), (2, 2)):
            actor, club_id, owner_id = people[seller_index]
            players = (
                await session.scalars(
                    select(Player)
                    .where(
                        Player.club_id == club_id,
                        Player.status == "active",
                        Player.position != "GK",
                        ~select(Auction.id)
                        .where(Auction.person_id == Player.person_id, Auction.status == "open")
                        .exists(),
                    )
                    .limit(needed)
                )
            ).all()
            for player in players:
                auctions.append(
                    (
                        await list_player(
                            session,
                            action(actor),
                            owner_id,
                            player.person_id,
                            100000,
                            clock=lambda: NOW,
                        )
                    ).id
                )
        buyer, _, owner_id = people[1]
        for auction_id in auctions[:5]:
            await place_bid(session, action(buyer), owner_id, auction_id, 100000, clock=lambda: NOW)
        with pytest.raises(ValueError):
            await place_bid(
                session, action(buyer), owner_id, auctions[5], 100000, clock=lambda: NOW
            )
        assert (await session.get(FinanceAccount, people[1][1])).reserved == 500000

    async def settle_one():
        from goaletto.application.match_runtime import NotReady

        try:
            return await close(sessions, first_id)
        except NotReady:
            return True

    await asyncio.wait_for(asyncio.gather(settle_one(), settle_one()), 10)
    await close(sessions, first_id)
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(TransferReceipt)) == 1
        assert (await session.get(Club, people[1][1])).budget == 950000
        assert (await session.get(FinanceAccount, people[1][1])).reserved == 400000
