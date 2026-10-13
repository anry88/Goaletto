import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from goaletto.application.game_actions import PermissionDenied, end_expired
from goaletto.application.training import apply_training, load_training, select_training, week_start
from goaletto.domain.training import SKILLS, Focus, Intensity, initial_caps
from goaletto.infrastructure.models import (
    Job,
    OwnershipPeriod,
    Player,
    PlayerDevelopment,
    TrainingDecision,
    TrainingPlan,
)
from tests.integration.test_agent_wave import NOW, setup_world

pytestmark = pytest.mark.integration


async def test_weekly_training_retry_fractional_progress_rollback_and_owner_expiry(sessions):
    _, actor, club_id = await setup_world(sessions)
    actor = replace(actor, operation_id=uuid4(), update_id=5)
    week = week_start(NOW)
    async with sessions.begin() as session:
        screen = await load_training(session, actor, record=False)
        owner_id = screen.ownership_id
        players = (
            await session.scalars(
                select(Player).where(Player.club_id == club_id).order_by(Player.person_id)
            )
        ).all()
        before = {p.person_id: (p.stamina, p.pace) for p in players}
        for p in players:
            dev = await session.get(PlayerDevelopment, p.person_id)
            assert dev.caps == initial_caps(p.person_id, {k: getattr(p, k) for k in SKILLS})
        first = await select_training(
            session,
            actor,
            ownership_id=owner_id,
            week=week,
            focus=Focus.PHYSICAL,
            intensity=Intensity.MEDIUM,
            clock=lambda: NOW,
        )
        plan_id = first.id
    changed_actor = replace(actor, operation_id=uuid4(), update_id=6)
    async with sessions.begin() as session:
        await select_training(
            session,
            changed_actor,
            ownership_id=owner_id,
            week=week,
            focus=Focus.PHYSICAL,
            intensity=Intensity.LOW,
            clock=lambda: NOW,
        )
        # Re-delivered older choice must not overwrite the later selection.
        repeated = await select_training(
            session,
            actor,
            ownership_id=owner_id,
            week=week,
            focus=Focus.PHYSICAL,
            intensity=Intensity.MEDIUM,
            clock=lambda: NOW,
        )
        assert repeated.intensity == Intensity.LOW
        assert await session.scalar(select(func.count()).select_from(TrainingPlan)) == 1
        assert await session.scalar(select(func.count()).select_from(TrainingDecision)) == 2
        jobs = (await session.scalars(select(Job).where(Job.kind == "training.apply"))).all()
        assert len(jobs) == 1
        job_id, due = jobs[0].id, jobs[0].due_at
    # Simulate failure after changes have flushed: no progress or receipt may survive.
    async with sessions() as session:
        await session.begin()
        await apply_training(session, await session.get(Job, job_id), due)
        await session.flush()
        await session.rollback()
    async with sessions.begin() as session:
        assert (await session.get(TrainingPlan, plan_id)).status == "scheduled"
        assert all(
            not any(d.progress.values())
            for d in (await session.scalars(select(PlayerDevelopment))).all()
        )
        job = await session.get(Job, job_id)
        await apply_training(session, job, due)
        await session.flush()
        plan = await session.get(TrainingPlan, plan_id)
        assert plan.status == "applied" and len(plan.results) == 20
        ownership = await session.get(OwnershipPeriod, owner_id)
        last_action = ownership.last_game_action_at
        assert last_action == NOW
        progress = {
            d.person_id: dict(d.progress)
            for d in (await session.scalars(select(PlayerDevelopment))).all()
        }
        assert any(progress[pid]["pace"] > 0 and progress[pid]["stamina"] > 0 for pid in before)
        await apply_training(session, job, due)
        await session.flush()
        assert progress == {
            d.person_id: dict(d.progress)
            for d in (await session.scalars(select(PlayerDevelopment))).all()
        }
        assert ownership.last_game_action_at == last_action
    # The next chosen week is cancelled after ownership expires, without training bots.
    later = due + timedelta(hours=1)
    later_actor = replace(actor, operation_id=uuid4(), at=later)
    async with sessions.begin() as session:
        second = await select_training(
            session,
            later_actor,
            ownership_id=owner_id,
            week=week_start(later),
            focus=Focus.TEAM,
            intensity=Intensity.MEDIUM,
            clock=lambda: later,
        )
        second_id = second.id
        ownership = await session.get(OwnershipPeriod, owner_id)
        expires = ownership.expires_at
        assert await end_expired(session, ownership, expires)
    async with sessions.begin() as session:
        job = await session.scalar(
            select(Job).where(
                Job.kind == "training.apply", Job.payload["plan_id"].astext == str(second_id)
            )
        )
        await apply_training(session, job, expires)
        assert (await session.get(TrainingPlan, second_id)).status == "cancelled"
        assert progress == {
            d.person_id: dict(d.progress)
            for d in (await session.scalars(select(PlayerDevelopment))).all()
        }


async def test_concurrent_training_save_worker_and_stale_executor(sessions):
    _, actor, club_id = await setup_world(sessions)
    actor = replace(actor, operation_id=uuid4(), update_id=5)
    async with sessions.begin() as session:
        screen = await load_training(session, actor, record=False)
        owner_id = screen.ownership_id
        foreign = await session.scalar(
            select(Player.person_id).where(Player.club_id != club_id).limit(1)
        )

    async def choose():
        async with sessions.begin() as session:
            return (
                await select_training(
                    session,
                    actor,
                    ownership_id=owner_id,
                    week=week_start(NOW),
                    focus=Focus.TEAM,
                    intensity=Intensity.HIGH,
                    clock=lambda: NOW,
                )
            ).id

    one, two = await asyncio.gather(choose(), choose())
    assert one == two
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(TrainingDecision)) == 1
        with pytest.raises(ValueError):
            await select_training(
                session,
                replace(actor, operation_id=uuid4()),
                ownership_id=owner_id,
                week=week_start(NOW),
                focus=Focus.SET_PIECES,
                intensity=Intensity.MEDIUM,
                executor_id=foreign,
                clock=lambda: NOW,
            )
        with pytest.raises(PermissionDenied):
            await select_training(
                session,
                replace(actor, operation_id=uuid4()),
                ownership_id=uuid4(),
                week=week_start(NOW),
                focus=Focus.TEAM,
                intensity=Intensity.MEDIUM,
                clock=lambda: NOW,
            )

    async def apply():
        async with sessions.begin() as session:
            job = await session.scalar(select(Job).where(Job.kind == "training.apply"))
            await apply_training(session, job, job.due_at)

    from goaletto.application.match_runtime import NotReady

    outcomes = await asyncio.gather(apply(), apply(), return_exceptions=True)
    assert all(o is None or isinstance(o, NotReady) for o in outcomes)
    async with sessions.begin() as session:
        plan = await session.get(TrainingPlan, one)
        assert plan.status == "applied" and len(plan.results) == 20
