"""Real transactional calendar, wage-unit and daily-training acceptance."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from goaletto.application.calendar_planning import plan_horizon, recover_unannounced_plan
from goaletto.application.finances import cycle_finances
from goaletto.application.kickoff_preferences import set_kickoff_preference
from goaletto.application.match_runtime import activate_season
from goaletto.application.onboarding import begin_game, claim_club
from goaletto.application.training import apply_training, load_training, select_training
from goaletto.application.world import initialize_world
from goaletto.domain.training import Focus, Intensity
from goaletto.domain.world import WorldOptions
from goaletto.infrastructure.models import (
    FinanceCycle,
    Fixture,
    FixturePlan,
    Job,
    LedgerEntry,
    PlayerRecovery,
    SalaryContract,
    Season,
)

pytestmark = pytest.mark.integration
AT = datetime(2026, 10, 12, 12, tzinfo=UTC)
FIRST = datetime(2026, 10, 17, 18, tzinfo=UTC)


async def setup(sessions):
    async with sessions.begin() as session:
        result = await initialize_world(
            session,
            WorldOptions(
                key="cycles",
                seed=7,
                first_kickoff=FIRST,
                initial_budget=600000,
            ),
            initialized_at=AT,
        )
        assert await session.scalar(select(func.count()).select_from(FixturePlan)) == 450
        assert await session.scalar(select(func.count()).select_from(Fixture)) == 0
        assert (
            await session.scalar(
                select(func.count()).select_from(Job).where(Job.kind == "match.tick")
            )
            == 0
        )
        await activate_season(session, result.season_id, AT)
        entry = await begin_game(session, telegram_id=1001, update_id=1, payload=None, at=AT)
        actor = replace(entry.actor, operation_id=uuid4(), update_id=2)
        owner = await claim_club(session, actor, entry.offers[0].id)
    return result, replace(actor, operation_id=uuid4()), owner.club_id


async def test_horizon_parallel_pinning_preferences_dst_and_conditional_slots(sessions):
    result, actor, club = await setup(sessions)
    async with sessions.begin() as session:
        first = dict((await session.execute(select(Fixture.id, Fixture.scheduled_start_at))).all())
        assert len(first) == 50  # Two rounds in [12th 12:00, 22nd 12:00).
        await set_kickoff_preference(
            session, replace(actor, operation_id=uuid4()), zone="Europe/Belgrade", minute=1200
        )
        season = await session.get(Season, result.season_id)
        session.add(
            FixturePlan(
                id=uuid4(),
                season_id=season.id,
                league_id=None,
                competition="cup",
                round_number=1,
                home_club_id=club,
                away_club_id=None,
                conditional_ref="winner:future-tie",
                assigned_day=datetime(2026, 10, 18).date(),
            )
        )
    later = AT + timedelta(days=4)

    async def run():
        async with sessions.begin() as session:
            return await plan_horizon(session, result.season_id, later)

    assert sum(await asyncio.gather(run(), run())) == 25
    async with sessions.begin() as session:
        fixtures = list((await session.scalars(select(Fixture))).all())
        assert len(fixtures) == 75
        assert all(f.scheduled_start_at == first[f.id] for f in fixtures if f.id in first)
        own = [
            f for f in fixtures if club in (f.home_club_id, f.away_club_id) and f.id not in first
        ]
        assert len(own) == 1 and own[0].scheduled_start_at == datetime(2026, 10, 25, 19, tzinfo=UTC)
        plans = list((await session.scalars(select(FixturePlan))).all())
        for plan in plans:
            if plan.announced_at is not None:
                fixture = await session.get(Fixture, plan.id)
                assert fixture.scheduled_start_at < plan.planning_until
        conditional = next(p for p in plans if p.competition == "cup")
        assert (
            conditional.announced_at is None
            and conditional.blocked_reason == "awaiting_participants"
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(Job).where(Job.kind == "match.tick")
            )
            == 75 * 30
        )


async def test_cycle_finances_partial_opening_and_four_day_salary_no_duplicate(sessions):
    result, actor, club = await setup(sessions)
    async with sessions.begin() as session:
        job = await session.scalar(
            select(Job).where(
                Job.kind == "finance.cycle", Job.payload["club_id"].astext == str(club)
            )
        )
        assert job.due_at == datetime(2026, 10, 14, tzinfo=UTC)
        contracts = list(
            (
                await session.scalars(select(SalaryContract).where(SalaryContract.club_id == club))
            ).all()
        )
        assert len(contracts) == 21
        assert all(c.policy_version == "economy-cycle-v2" for c in contracts)
        await cycle_finances(session, job, job.due_at)
        receipt = await session.scalar(select(FinanceCycle).where(FinanceCycle.club_id == club))
        assert all(row["period_seconds"] == 345600 for row in receipt.snapshot["contracts"])
        assert all(row["seconds"] == 36 * 3600 for row in receipt.snapshot["contracts"])
        before = await session.scalar(select(func.count()).select_from(LedgerEntry))
        await cycle_finances(session, job, job.due_at)
        assert await session.scalar(select(func.count()).select_from(LedgerEntry)) == before
        job.status = "done"
        next_job = await session.scalar(
            select(Job).where(
                Job.kind == "finance.cycle",
                Job.payload["club_id"].astext == str(club),
                Job.due_at > job.due_at,
            )
        )
        assert next_job.due_at == datetime(2026, 10, 18, tzinfo=UTC)


async def test_daily_training_uses_free_date_and_does_not_apply_weekly_growth(sessions):
    result, actor, club = await setup(sessions)
    async with sessions.begin() as session:
        screen = await load_training(session, actor)
        assert screen.week_start == AT.replace(hour=0)
        plan = await select_training(
            session,
            replace(actor, operation_id=uuid4()),
            ownership_id=screen.ownership_id,
            week=screen.week_start,
            focus=Focus.TEAM,
            intensity=Intensity.MEDIUM,
            clock=lambda: AT,
        )
        assert plan.policy_version == "training-specialists-v2"
        job = await session.scalar(
            select(Job).where(
                Job.kind == "training.apply", Job.payload["plan_id"].astext == str(plan.id)
            )
        )
        await apply_training(session, job, job.due_at)
        assert plan.status == "applied" and max(r["gain_bps"] for r in plan.results) < 1000
        before = plan.results
        energy_before = {
            row.person_id: row.energy_bps
            for row in (await session.scalars(select(PlayerRecovery))).all()
        }
        assert any(value == 9860 for value in energy_before.values())
        await apply_training(session, job, job.due_at)
        assert plan.results == before
        assert energy_before == {
            row.person_id: row.energy_bps
            for row in (await session.scalars(select(PlayerRecovery))).all()
        }
        actor = replace(actor, at=FIRST, operation_id=uuid4())
        screen = await load_training(session, actor)
        assert screen.blocked
        with pytest.raises(ValueError, match="match day"):
            await select_training(
                session,
                actor,
                ownership_id=screen.ownership_id,
                week=screen.week_start,
                focus=Focus.TEAM,
                intensity=Intensity.MEDIUM,
                clock=lambda: FIRST,
            )


async def test_exact_horizon_boundary_missed_window_and_announced_recovery_guard(sessions):
    result, _, _ = await setup(sessions)
    boundary = FIRST - timedelta(days=2)
    async with sessions.begin() as session:
        # Round three sits exactly at planning_at + 10 days and stays unfixed.
        assert await plan_horizon(session, result.season_id, boundary) == 0
        assert await plan_horizon(session, result.season_id, boundary + timedelta(seconds=1)) == 25
        missed_at = FIRST + timedelta(days=12)
        await plan_horizon(session, result.season_id, missed_at)
        missed = list(
            (await session.scalars(select(FixturePlan).where(FixturePlan.round_number == 4))).all()
        )
        assert len(missed) == 25
        assert all(p.blocked_reason == "missed_preparation_window" for p in missed)
        assert all(p.announced_at is None for p in missed)
        announced = await session.scalar(
            select(FixturePlan).where(FixturePlan.announced_at.is_not(None)).limit(1)
        )
        with pytest.raises(ValueError, match="unannounced plan"):
            await recover_unannounced_plan(
                session, announced.id, missed_at + timedelta(days=4), missed_at
            )
