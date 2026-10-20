import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from goaletto.application.club_valuation import valuation
from goaletto.application.finances import close_salary
from goaletto.application.game_actions import PermissionDenied, owned_club
from goaletto.application.market import repair_lineup
from goaletto.application.money import (
    InsufficientFunds,
    credit_locked,
    debit_locked,
    locked_accounts,
    reserve,
)
from goaletto.application.onboarding import ClubUnavailable, claim_club
from goaletto.application.outbox_delivery import Decision, check_recipient, render_text
from goaletto.domain.game_cycle import midnight
from goaletto.infrastructure.models import (
    BankruptcyReceipt,
    Club,
    FinanceAccount,
    LedgerEntry,
    Manager,
    MoneyReservation,
    OutboxMessage,
    OwnershipPeriod,
    Person,
    Player,
    Season,
    Staff,
)
from tests.integration.test_agent_wave import setup_world

pytestmark = pytest.mark.integration


async def test_credit_boundary_bankruptcy_retry_rollback_and_notice(sessions):
    result, actor, club_id = await setup_world(sessions)
    async with sessions.begin() as session:
        account = (await locked_accounts(session, club_id))[club_id]
        limit = account.record.credit_limit
        assert limit == sum((await valuation(session, account.club)).values())
        assert limit >= 2_400_000  # includes starting stadium + academy + squad
        amount = account.club.budget + limit
        await debit_locked(
            session, account, "edge", "player_salary", amount, actor.at, mandatory=True
        )
    async with sessions.begin() as session:
        account = (await locked_accounts(session, club_id))[club_id]
        assert account.club.budget == -limit and account.record.arrears == 0
        assert (await owned_club(session, actor))[0].ended_at is None
    with pytest.raises(RuntimeError):
        async with sessions.begin() as session:
            account = (await locked_accounts(session, club_id))[club_id]
            await debit_locked(
                session, account, "over", "player_salary", 1, actor.at, mandatory=True
            )
            raise RuntimeError("rollback")
    async with sessions.begin() as session:
        account = (await locked_accounts(session, club_id))[club_id]
        assert account.club.budget == -limit
        await debit_locked(session, account, "over", "player_salary", 1, actor.at, mandatory=True)
    async with sessions.begin() as session:
        club = await session.get(Club, club_id)
        assert club.budget == -limit - 1 and club.owner_telegram_id is None
        owner = await session.scalar(
            select(OwnershipPeriod).where(OwnershipPeriod.club_id == club_id)
        )
        assert owner.end_reason == "bankrupt"
        season = await session.get(Season, result.season_id)
        assert (await session.get(Manager, actor.manager_id)).bankruptcy_until == midnight(
            season.cycle_epoch
        )
        assert await session.scalar(select(func.count()).select_from(BankruptcyReceipt)) == 1
        notice = await session.scalar(
            select(OutboxMessage).where(OutboxMessage.payload["kind"].astext == "bankruptcy")
        )
        assert await check_recipient(session, notice, actor.at) == Decision.DELIVER
        assert "банкрот" in render_text(notice.payload)
        account = (await locked_accounts(session, club_id))[club_id]
        assert not await debit_locked(
            session, account, "over", "player_salary", 1, actor.at, mandatory=True
        )
    with pytest.raises(PermissionDenied):
        async with sessions.begin() as session:
            await owned_club(session, actor)
    with pytest.raises(ClubUnavailable):
        async with sessions.begin() as session:
            await claim_club(session, replace(actor, operation_id=uuid4()), club_id)
    async with sessions.begin() as session:
        account = (await locked_accounts(session, club_id))[club_id]
        await credit_locked(session, account, "repay", "sponsor", limit + 1, actor.at)
    async with sessions.begin() as session:
        club = await session.get(Club, club_id)
        assert club.budget == 0 and club.bankrupt_at is None
        assert await session.scalar(select(func.count()).select_from(BankruptcyReceipt)) == 1


async def test_final_valuation_change_and_concurrent_cash_reservations(sessions):
    result, actor, club_id = await setup_world(sessions)
    async with sessions.begin() as session:
        account = (await locked_accounts(session, club_id))[club_id]
        amount = account.club.budget
        mandatory_amount = amount + account.record.credit_limit

    async def hold():
        try:
            async with sessions.begin() as session:
                await reserve(session, club_id, uuid4(), amount, {"test": "credit"}, actor.at)
            return True
        except InsufficientFunds:
            return False

    assert sorted(await asyncio.gather(hold(), hold())) == [False, True]
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(MoneyReservation)) == 1
        # Mandatory spending must release an uncovered market hold; this standalone
        # reservation is explicitly released because it is not an auction.
        from goaletto.application.money import release

        reservation = await session.scalar(select(MoneyReservation))
        await release(session, club_id, reservation.id, actor.at)
        account = (await locked_accounts(session, club_id))[club_id]
        await debit_locked(
            session, account, "edge", "player_salary", mandatory_amount, actor.at, mandatory=True
        )
    async with sessions.begin() as session:
        player = await session.scalar(select(Player).where(Player.club_id == club_id))
        player.status, player.last_season_id = "last_season", result.season_id
        person_id = player.person_id
        worker = await session.scalar(select(Staff).where(Staff.club_id == club_id))
        worker.status, worker.last_season_id = "last_season", result.season_id
    async with sessions.begin() as session:
        account = (await locked_accounts(session, club_id))[club_id]
        assert account.club.owner_telegram_id == actor.telegram_id
        assert account.club.budget == -account.record.credit_limit
        # Remove the retired asset. Full lifecycle/lineup repair has separate season coverage.
        await repair_lineup(session, club_id, person_id, None, actor.at)
        await close_salary(session, person_id, actor.at)
        player = await session.get(Player, person_id)
        player.status, player.retired_after_season_id, player.club_id = (
            "retired",
            result.season_id,
            None,
        )
        (await session.get(Person, person_id)).career = "retired"
    async with sessions.begin() as session:
        assert (await session.get(Club, club_id)).owner_telegram_id is None
        assert (await session.get(Player, person_id)).status == "retired"
        account = await session.get(FinanceAccount, club_id)
        latest = await session.scalar(
            select(LedgerEntry)
            .where(LedgerEntry.club_id == club_id)
            .order_by(LedgerEntry.sequence.desc())
            .limit(1)
        )
        assert latest.balance_after == (await session.get(Club, club_id)).budget
        assert account.reserved == 0
