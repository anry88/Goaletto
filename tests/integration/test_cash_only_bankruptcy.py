"""Cash-funded acquisitions, and mandatory debt cancellation using real market/scout flows."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select

from goaletto.application.market import place_bid
from goaletto.application.money import InsufficientFunds, debit_locked, locked_accounts, reserve
from goaletto.application.scouting import start_search
from goaletto.domain.finances import scout_mission_fee
from goaletto.infrastructure.models import (
    Auction,
    BankruptcyReceipt,
    Club,
    FinanceAccount,
    LedgerEntry,
    MoneyReservation,
    Player,
    ScoutMission,
)
from tests.integration.test_agent_wave import NOW, setup_world
from tests.integration.test_market import action, market_world
from tests.integration.test_scouting import ready

pytestmark = pytest.mark.integration


async def test_credit_does_not_fund_any_optional_expense_or_bid(sessions):
    _, actor, club = await setup_world(sessions)
    async with sessions.begin() as session:
        account = (await locked_accounts(session, club))[club]
        cash = account.club.budget
        assert account.record.credit_limit > cash
        for kind in ("signing_fee", "stadium_upgrade", "academy_upgrade", "scout_search"):
            with pytest.raises(InsufficientFunds):
                await debit_locked(session, account, "too-much-" + kind, kind, cash + 1, NOW)
            with pytest.raises(ValueError, match="Only wages and upkeep"):
                await debit_locked(
                    session, account, "invalid-" + kind, kind, 1, NOW, mandatory=True
                )
        with pytest.raises(InsufficientFunds):
            await reserve(session, club, uuid4(), cash + 1, {"test": "no-credit"}, NOW)
        await debit_locked(
            session, account, "salary-debt", "staff_salary", cash + 1, NOW, mandatory=True
        )
    async with sessions.begin() as session:
        account = (await locked_accounts(session, club))[club]
        assert account.club.budget == -1 and account.available == 0
        with pytest.raises(InsufficientFunds):
            await debit_locked(session, account, "no-cash", "signing_fee", 1, NOW)
        assert await session.scalar(select(func.count()).select_from(BankruptcyReceipt)) == 0


@pytest.mark.parametrize("insolvent", [False, True])
async def test_wages_cancel_uncovered_real_auction_without_transfer(sessions, insolvent):
    people, auction_id, person = await market_world(sessions)
    seller, buyer = people[0], people[1]
    async with sessions.begin() as session:
        await place_bid(session, action(buyer[0]), buyer[2], auction_id, 100000, clock=lambda: NOW)
    async with sessions.begin() as session:
        account = (await locked_accounts(session, buyer[1]))[buyer[1]]
        amount = account.club.budget + (account.record.credit_limit + 1 if insolvent else 1)
        await debit_locked(session, account, "wages", "player_salary", amount, NOW, mandatory=True)
    async with sessions.begin() as session:
        auction = await session.get(Auction, auction_id)
        assert auction.status == "cancelled"
        assert (await session.get(Player, person)).club_id == seller[1]
        assert (await session.get(FinanceAccount, buyer[1])).reserved == 0
        assert (await session.scalar(select(MoneyReservation))).status == "released"
        assert (await session.get(Club, buyer[1])).owner_telegram_id == (
            None if insolvent else buyer[0].telegram_id
        )
        assert await session.scalar(select(func.count()).select_from(BankruptcyReceipt)) == int(
            insolvent
        )
        count = await session.scalar(select(func.count()).select_from(LedgerEntry))
        account = (await locked_accounts(session, buyer[1]))[buyer[1]]
        assert not await debit_locked(
            session, account, "wages", "player_salary", amount, NOW, mandatory=True
        )
        assert await session.scalar(select(func.count()).select_from(LedgerEntry)) == count


async def test_bankruptcy_stops_paid_scout_search_without_refund_or_additional_charge(sessions):
    actor, club, owner, scout, skill = await ready(sessions)
    async with sessions.begin() as session:
        mission = await start_search(
            session, action(actor), owner, scout, "FC", 4, scout_mission_fee(skill, 4)
        )
        mission_id = mission.id
    async with sessions.begin() as session:
        account = (await locked_accounts(session, club))[club]
        final_cash = -account.record.credit_limit - 1
        await debit_locked(
            session,
            account,
            "insolvent",
            "academy_upkeep",
            account.club.budget - final_cash,
            NOW,
            mandatory=True,
        )
    async with sessions.begin() as session:
        assert (await session.get(ScoutMission, mission_id)).status == "cancelled"
        assert (await session.get(Club, club)).budget == final_cash
        assert await session.scalar(select(func.count()).select_from(BankruptcyReceipt)) == 1
