"""Paid expansion races, pinned match crowd and delayed weekly facility bills."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.finances import weekly_finances
from goaletto.application.game_actions import end_expired
from goaletto.application.money import (
    InsufficientFunds,
    credit_locked,
    locked_accounts,
    release,
    reserve,
)
from goaletto.application.stadium import complete_expansion, load_stadium, order_expansion
from goaletto.domain.match_codec import restore_state, state_hash
from goaletto.infrastructure.models import (
    Club,
    FinanceCycle,
    Job,
    LedgerEntry,
    Match,
    MatchCrowd,
    OwnershipPeriod,
    StadiumExpansion,
)
from tests.integration.test_agent_wave import NOW, setup_world, start_fixture

pytestmark = pytest.mark.integration


async def financed(sessions):
    result, actor, club_id = await setup_world(sessions)
    async with sessions.begin() as session:
        account = (await locked_accounts(session, club_id))[club_id]
        await credit_locked(session, account, "test-capital", "grant", 1000000, NOW)
        owner = await session.scalar(
            select(OwnershipPeriod).where(
                OwnershipPeriod.club_id == club_id, OwnershipPeriod.ended_at.is_(None)
            )
        )
        owner_id = owner.id
    return result, actor, club_id, owner_id


async def test_parallel_upgrade_one_payment_job_and_idempotent_owner_quote(sessions):
    _, actor, club_id, owner = await financed(sessions)
    actors = [replace(actor, operation_id=uuid4(), update_id=100 + i) for i in range(2)]

    async def order(actor):
        try:
            async with sessions.begin() as session:
                return (await order_expansion(session, actor, owner, 5000, clock=lambda: NOW)).id
        except ValueError:
            return None

    results = await asyncio.wait_for(asyncio.gather(*(order(a) for a in actors)), 10)
    assert sum(r is not None for r in results) == 1
    winner = results.index(next(r for r in results if r is not None))
    async with sessions.begin() as session:
        expansion = await order_expansion(session, actors[winner], owner, 5000, clock=lambda: NOW)
        assert expansion.id == results[winner]
        club = await session.get(Club, club_id)
        assert club.budget == 750000 and club.stadium_capacity == 5000
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LedgerEntry)
                .where(LedgerEntry.kind == "stadium_upgrade")
            )
            == 1
        )
        work = await session.scalar(select(Job).where(Job.kind == "stadium.complete"))
        await complete_expansion(session, work, work.due_at)
        await complete_expansion(session, work, work.due_at + timedelta(days=2))
        assert club.stadium_capacity == 10000 and club.budget == 750000
        # Retrying the original action after completion never orders a second level.
        assert (
            await order_expansion(session, actors[winner], owner, 5000, clock=lambda: NOW)
        ).id == expansion.id
        ownership = await session.get(OwnershipPeriod, owner)
        assert ownership.last_game_action_at == NOW
        with pytest.raises(ValueError):
            await order_expansion(
                session, replace(actor, operation_id=uuid4()), owner, 5000, clock=lambda: NOW
            )
        with pytest.raises(ValueError):
            await order_expansion(
                session, replace(actor, operation_id=uuid4()), uuid4(), 10000, clock=lambda: NOW
            )
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(text("UPDATE stadium_expansions SET cost=1"))


async def test_reservations_block_upgrade_and_abort_rolls_back_payment_and_job(sessions):
    _, actor, club_id = await setup_world(sessions)
    hold = uuid4()
    async with sessions.begin() as session:
        account = (await locked_accounts(session, club_id))[club_id]
        await credit_locked(session, account, "test-capital", "grant", 300000, NOW)
        await reserve(session, club_id, hold, 100000, {"auction": "test"}, NOW)
        owner = await session.scalar(
            select(OwnershipPeriod.id).where(
                OwnershipPeriod.club_id == club_id, OwnershipPeriod.ended_at.is_(None)
            )
        )
        with pytest.raises(InsufficientFunds):
            await order_expansion(
                session, replace(actor, operation_id=uuid4()), owner, 5000, clock=lambda: NOW
            )
        await release(session, club_id, hold, NOW)
    with pytest.raises(RuntimeError):
        async with sessions.begin() as session:
            await order_expansion(
                session,
                replace(actor, operation_id=uuid4(), update_id=100),
                owner,
                5000,
                clock=lambda: NOW,
            )
            raise RuntimeError("Abort order transaction")
    async with sessions.begin() as session:
        assert (await session.get(Club, club_id)).budget == 350000
        assert await session.scalar(select(func.count()).select_from(StadiumExpansion)) == 0
        assert (
            await session.scalar(
                select(func.count()).select_from(Job).where(Job.kind == "stadium.complete")
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(LedgerEntry)
                .where(LedgerEntry.kind == "stadium_upgrade")
            )
            == 0
        )


async def test_crowd_is_pinned_across_capacity_change_and_old_invoice_uses_history(sessions):
    _, actor, club_id, owner = await financed(sessions)
    at = NOW + timedelta(days=3)
    async with sessions.begin() as session:
        expansion = await order_expansion(
            session,
            replace(actor, at=at, operation_id=uuid4(), update_id=100),
            owner,
            5000,
            clock=lambda: at,
        )
        completed_at = expansion.due_at
    fixture, _ = await start_fixture(sessions, club_id)
    async with sessions.begin() as session:
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture))
        original = state_hash(restore_state(match.starting_snapshot))
        crowd = await session.get(MatchCrowd, match.id)
        assert (
            crowd.policy_version == "stadium-demand-v1"
            and crowd.basis["fans"] == restore_state(match.state).crowd.home_fans
        )
        work = await session.scalar(select(Job).where(Job.kind == "stadium.complete"))
        await complete_expansion(session, work, completed_at)
        assert (await session.get(Club, club_id)).stadium_capacity == 10000
        assert state_hash(restore_state(match.starting_snapshot)) == original
        finance_job = await session.scalar(
            select(Job).where(
                Job.kind == "finance.weekly", Job.payload["club_id"].astext == str(club_id)
            )
        )
        await weekly_finances(session, finance_job, completed_at + timedelta(hours=1))
        finance_job.status = "done"
        first = await session.scalar(select(FinanceCycle).where(FinanceCycle.club_id == club_id))
        assert first.snapshot["amounts"]["stadium_upkeep"] == 3000
        next_job = await session.scalar(
            select(Job).where(
                Job.kind == "finance.weekly",
                Job.payload["club_id"].astext == str(club_id),
                Job.due_at == finance_job.due_at + timedelta(days=7),
            )
        )
        await weekly_finances(session, next_job, next_job.due_at)
        second = await session.scalar(
            select(FinanceCycle).where(
                FinanceCycle.club_id == club_id, FinanceCycle.week_start == next_job.due_at
            )
        )
        assert second.snapshot["amounts"]["stadium_upkeep"] == 3500
        assert [p["capacity"] for p in second.snapshot["capacity_periods"]] == [5000, 10000]
        screen = await load_stadium(
            session, replace(actor, at=next_job.due_at, operation_id=uuid4(), update_id=102)
        )
        assert screen.capacity == 10000
    with pytest.raises(DBAPIError):
        async with sessions.begin() as session:
            await session.execute(text("UPDATE match_crowds SET basis='{}'::jsonb"))


async def test_paid_building_completes_after_ownership_expires_without_extending_it(sessions):
    _, actor, club_id, owner_id = await financed(sessions)
    async with sessions.begin() as session:
        await order_expansion(
            session,
            replace(actor, operation_id=uuid4(), update_id=100),
            owner_id,
            5000,
            clock=lambda: NOW,
        )
        owner = await session.get(OwnershipPeriod, owner_id)
        at = owner.expires_at
        assert await end_expired(session, owner, at)
        last_action = owner.last_game_action_at
        work = await session.scalar(select(Job).where(Job.kind == "stadium.complete"))
        await complete_expansion(session, work, at)
        club = await session.get(Club, club_id)
        assert club.owner_telegram_id is None and club.stadium_capacity == 10000
        assert owner.ended_at is not None and owner.last_game_action_at == last_action
