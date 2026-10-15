"""Money races, invoice history, rollback and league rewards on the single app DB."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.calendar_planning import plan_horizon
from goaletto.application.finances import (
    close_salary,
    cycle_finances,
    load_finances,
    pay_league_prizes,
)
from goaletto.application.money import (
    InsufficientFunds,
    credit_locked,
    debit_locked,
    locked_accounts,
    release,
    reserve,
    settle,
)
from goaletto.application.results import finalize_result
from goaletto.domain.finances import season_prize
from goaletto.domain.match_codec import restore_state
from goaletto.infrastructure.models import (
    Club,
    FinanceCycle,
    Fixture,
    Job,
    LedgerEntry,
    Manager,
    Match,
    MatchResult,
    OwnershipPeriod,
    SalaryContract,
    Season,
    Standing,
)
from tests.integration.test_agent_wave import NOW, job, setup_world, start_fixture

pytestmark = pytest.mark.integration


async def test_concurrent_reserves_settle_once_and_rollback(sessions):
    _, _, club_id = await setup_world(sessions)
    ids = (uuid4(), uuid4())

    async def attempt(key):
        try:
            async with sessions.begin() as session:
                await reserve(session, club_id, key, 40000, {"auction": "test"}, NOW)
            return True
        except InsufficientFunds:
            return False

    result = await asyncio.wait_for(asyncio.gather(*(attempt(key) for key in ids)), 10)
    assert sum(result) == 1
    winning = ids[result.index(True)]
    async with sessions.begin() as session:
        receiver = await session.scalar(
            select(Club.id).where(Club.id != club_id).order_by(Club.id).limit(1)
        )
        assert await settle(session, club_id, receiver, winning, NOW)
        assert not await settle(session, club_id, receiver, winning, NOW)
        account = (await locked_accounts(session, club_id))[club_id]
        assert account.club.budget == 10000 and account.record.reserved == 0
        other = (await locked_accounts(session, receiver))[receiver]
        assert other.club.budget == 90000
        with pytest.raises(ValueError, match="different scope"):
            await credit_locked(session, other, f"settle:{winning}", "transfer_in", 39999, NOW)
    with pytest.raises(RuntimeError):
        async with sessions.begin() as session:
            await reserve(session, club_id, uuid4(), 5000, {"auction": "rollback"}, NOW)
            raise RuntimeError("Abort full money transaction")
    async with sessions.begin() as session:
        account = (await locked_accounts(session, club_id))[club_id]
        assert (account.club.budget, account.record.reserved) == (10000, 0)
        entries = (
            await session.scalars(
                select(LedgerEntry)
                .where(LedgerEntry.club_id == club_id)
                .order_by(LedgerEntry.sequence)
            )
        ).all()
        assert sum(e.cash_delta for e in entries) == account.club.budget
        assert [e.sequence for e in entries] == list(range(1, account.record.sequence + 1))


async def test_bills_protect_reservations_and_income_repays_arrears(sessions):
    _, _, club_id = await setup_world(sessions)
    key = uuid4()
    async with sessions.begin() as session:
        await reserve(session, club_id, key, 40000, {"auction": "test"}, NOW)
        account = (await locked_accounts(session, club_id))[club_id]
        await debit_locked(
            session, account, "test-wages", "player_salary", 60000, NOW, mandatory=True
        )
        assert (account.club.budget, account.record.reserved, account.record.arrears) == (
            40000,
            40000,
            50000,
        )
        assert not await debit_locked(
            session, account, "test-wages", "player_salary", 60000, NOW, mandatory=True
        )
        with pytest.raises(InsufficientFunds):
            await debit_locked(session, account, "upgrade", "stadium_upgrade", 1, NOW)
        assert await release(session, club_id, key, NOW)
        assert not await release(session, club_id, key, NOW)
        account = (await locked_accounts(session, club_id))[club_id]
        assert (account.club.budget, account.record.arrears) == (0, 10000)
        await credit_locked(session, account, "grant", "grant", 20000, NOW)
        assert (account.club.budget, account.record.arrears) == (10000, 0)
        assert not await credit_locked(session, account, "grant", "grant", 20000, NOW)
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(
                text("UPDATE ledger_entries SET cash_delta=1 WHERE club_id=:club"),
                {"club": club_id},
            )
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(
                text("UPDATE salary_contracts SET weekly_rate=1 WHERE club_id=:club"),
                {"club": club_id},
            )


async def test_cycle_invoice_fixed_prorated_contracts_idempotent_and_explicit_read(sessions):
    _, actor, club_id = await setup_world(sessions)
    async with sessions.begin() as session:
        manager = await session.get(Manager, actor.manager_id)
        ownership = await session.scalar(
            select(OwnershipPeriod).where(
                OwnershipPeriod.club_id == club_id, OwnershipPeriod.ended_at.is_(None)
            )
        )
        original_activity = ownership.last_game_action_at
        contract = await session.scalar(
            select(SalaryContract)
            .where(SalaryContract.club_id == club_id)
            .order_by(SalaryContract.id)
            .limit(1)
        )
        rate = contract.weekly_rate
        await close_salary(session, contract.person_id, NOW + timedelta(days=3, hours=12))
        finance_job = await session.scalar(
            select(Job).where(
                Job.kind == "finance.cycle", Job.payload["club_id"].astext == str(club_id)
            )
        )
        assert finance_job.status == "pending"
        at = finance_job.due_at
        await cycle_finances(session, finance_job, at)
        balance = (await locked_accounts(session, club_id))[club_id].club.budget
        finance_job.status = "done"
        await cycle_finances(session, finance_job, at + timedelta(days=10))
        assert (await locked_accounts(session, club_id))[club_id].club.budget == balance
        cycle = await session.scalar(select(FinanceCycle).where(FinanceCycle.club_id == club_id))
        period_start = datetime.fromisoformat(finance_job.payload["period_start"])
        seconds = int(
            (min(contract.ended_at, at) - max(contract.started_at, period_start)).total_seconds()
        )
        expected = (rate * seconds + 345599) // 345600
        saved = next(row for row in cycle.snapshot["contracts"] if row["id"] == str(contract.id))
        assert saved["due"] == expected and saved["rate"] == rate
        assert ownership.last_game_action_at == original_activity
        assert (
            await session.scalar(
                select(func.count())
                .select_from(FinanceCycle)
                .where(FinanceCycle.club_id == club_id)
            )
            == 1
        )
        screen = await load_finances(
            session, replace(actor, at=at + timedelta(hours=1), operation_id=uuid4(), update_id=101)
        )
        assert (
            screen.balance == balance
            and screen.last_cycle.id == cycle.id
            and screen.due_at == at + timedelta(days=4)
        )
        assert ownership.last_game_action_at > original_activity
        with pytest.raises(ValueError, match="earlier owner"):
            await load_finances(session, actor, ownership_id=uuid4())
        assert manager is not None


async def test_match_income_atomic_once_with_final_result(sessions):
    _, _, club_id = await setup_world(sessions)
    fixture_id, _ = await start_fixture(sessions, club_id)
    for tick in range(1, 31):
        if tick == 16:
            await job(sessions, "match.second_half_start", fixture_id)
        at = await job(sessions, "match.tick", fixture_id, tick)
    async with sessions.begin() as session:
        fixture = await session.get(Fixture, fixture_id)
        account = (await locked_accounts(session, fixture.home_club_id))[fixture.home_club_id]
        income = await session.scalar(
            select(LedgerEntry).where(LedgerEntry.operation_key == f"tickets:{fixture_id}")
        )
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture_id))
        expected = restore_state(match.state).crowd.home_fans * 5
        assert income.cash_delta == expected
        assert account.club.budget == 50000 + expected
        assert not await finalize_result(session, fixture_id, at + timedelta(minutes=1))
        assert account.club.budget == 50000 + expected
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LedgerEntry)
                .where(LedgerEntry.operation_key == f"tickets:{fixture_id}")
            )
            == 1
        )


async def test_league_prizes_require_full_results_and_pay_once(sessions):
    result, _, club_id = await setup_world(sessions)
    async with sessions.begin() as session:
        season = await session.get(Season, result.season_id)
        for offset in range(18):
            await plan_horizon(
                session,
                season.id,
                season.starts_at - timedelta(days=1) + timedelta(days=4 * offset),
            )
        club = await session.get(Club, club_id)
        assert not await pay_league_prizes(session, result.season_id, club.league_id, NOW)
        fixtures = (
            await session.scalars(select(Fixture).where(Fixture.league_id == club.league_id))
        ).all()
        # Persist a complete deterministic 0:0 season fixture for financial settlement checks.
        for fixture in fixtures:
            fixture.status = "finished"
            fixture.actual_start_at = fixture.scheduled_start_at
            fixture.actual_finish_at = fixture.scheduled_start_at + timedelta(minutes=33)
            match = Match(
                id=uuid4(),
                fixture_id=fixture.id,
                seed=0,
                engine_version="regulation-v1",
                state_version=1,
                state={},
                starting_snapshot={},
                completed_tick=30,
                phase="full_time",
                home_goals=0,
                away_goals=0,
            )
            session.add(match)
            await session.flush()
            session.add(
                MatchResult(
                    match_id=match.id,
                    fixture_id=fixture.id,
                    season_id=fixture.season_id,
                    league_id=fixture.league_id,
                    home_club_id=fixture.home_club_id,
                    away_club_id=fixture.away_club_id,
                    version=1,
                    home_goals=0,
                    away_goals=0,
                    actual_start_at=fixture.actual_start_at,
                    actual_finish_at=fixture.actual_finish_at,
                    recorded_at=fixture.actual_finish_at,
                    state_hash="0" * 64,
                )
            )
        rows = (
            await session.scalars(
                select(Standing).where(
                    Standing.season_id == result.season_id,
                    Standing.club_id.in_(select(Club.id).where(Club.league_id == club.league_id)),
                )
            )
        ).all()
        for row in rows:
            row.played = row.drawn = row.points = 18
        await session.flush()
        at = max(f.actual_finish_at for f in fixtures)
        from goaletto.application.results import league_table

        first = (await league_table(session, result.season_id, club.league_id))[0]
        account = (await locked_accounts(session, first.club_id))[first.club_id]
        await credit_locked(
            session,
            account,
            f"prize:{result.season_id}:{club.league_id}",
            "prize",
            250000,
            at,
            {
                "season_id": str(result.season_id),
                "league_id": str(club.league_id),
                "place": 1,
                "policy": "economy-cycle-v1",
            },
        )
        assert await pay_league_prizes(session, result.season_id, club.league_id, at)
        assert not await pay_league_prizes(session, result.season_id, club.league_id, at)
        prizes = (
            await session.scalars(select(LedgerEntry).where(LedgerEntry.kind == "prize"))
        ).all()
        assert len(prizes) == 10 and sum(p.cash_delta for p in prizes) == 250000 + sum(
            season_prize(5, place) for place in range(2, 11)
        )
        assert len({p.request["details"]["place"] for p in prizes}) == 10
