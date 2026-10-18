"""Repair a lost continuation, not its checkpoint or result, on the single DB."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from goaletto.application.runtime_reconciliation import JOB_ID, request_reconciliation
from goaletto.domain.match_codec import restore_state, state_hash
from goaletto.infrastructure.jobs import process_one_job
from goaletto.infrastructure.models import Job, Match, MatchRecovery, OwnershipPeriod, Season
from tests.integration.test_live_commands import live
from tests.integration.test_live_recovery import tick_job

pytestmark = pytest.mark.integration


async def prepare(sessions):
    actor, fixture, at, screen = await live(sessions)
    discovery = at + timedelta(minutes=5)
    async with sessions.begin() as session:
        await session.execute(update(Season).values(calendar_enabled=True))
        await session.execute(update(Job).where(Job.status == "pending").values(status="held"))
        missing = await tick_job(session, fixture, 1)
        await session.delete(missing)
        await request_reconciliation(session, discovery)
    return actor, fixture, discovery, screen


async def test_missing_transition_repaired_once_after_rollback_then_restores_decision_window(
    sessions,
):
    actor, fixture, discovery, screen = await prepare(sessions)
    async with sessions() as session:
        assert await process_one_job(session, at=discovery)
        await session.rollback()
    async with sessions.begin() as session:
        assert (await session.get(Job, JOB_ID)).attempts == 0
        assert await tick_job(session, fixture, 1) is None
        previous_hash = state_hash(restore_state((await session.get(Match, screen.match_id)).state))
        activity = (await session.get(OwnershipPeriod, screen.ownership_id)).last_game_action_at

    async def worker():
        async with sessions.begin() as session:
            return await process_one_job(session, at=discovery)

    assert any(await asyncio.gather(worker(), worker()))
    async with sessions.begin() as session:
        root = await session.get(Job, JOB_ID)
        assert root.payload["restored"] == 1
        rows = (
            await session.scalars(select(Job).where(Job.dedupe_key.like("reconcile:v1:%")))
        ).all()
        assert len(rows) == 1 and rows[0].status == "pending"
        # Recovery uses the ordinary handler: no missed tick is simulated now.
        match = await session.get(Match, screen.match_id)
        if match.next_due_at <= discovery:
            assert await process_one_job(session, at=discovery)
            await session.refresh(match)
        assert match.completed_tick == 0 and match.next_due_at == discovery + timedelta(seconds=60)
        assert state_hash(restore_state(match.state)) == previous_hash
        assert await session.scalar(select(func.count()).select_from(MatchRecovery)) == 1
        owner = await session.get(OwnershipPeriod, screen.ownership_id)
        assert owner.last_game_action_at == activity


@pytest.mark.parametrize("guard", ["corrupt", "score_mismatch", "paused", "failed_job"])
async def test_reconciliation_never_resets_failed_jobs_or_repairs_unproven_state(sessions, guard):
    _, fixture, discovery, screen = await prepare(sessions)
    async with sessions.begin() as session:
        match = await session.get(Match, screen.match_id)
        if guard == "corrupt":
            match.state = {"broken": True}
        elif guard == "score_mismatch":
            match.home_goals += 1
        elif guard == "paused":
            from goaletto.application.match_runtime import pause_match

            await pause_match(session, fixture, discovery)
        else:
            session.add(
                Job(
                    id=uuid4(),
                    dedupe_key="failed-tick",
                    kind="match.tick",
                    due_at=match.next_due_at,
                    status="failed",
                    attempts=1,
                    payload={"fixture_id": str(fixture), "tick_number": 1},
                )
            )
        assert await process_one_job(session, at=discovery)
    async with sessions.begin() as session:
        root = await session.get(Job, JOB_ID)
        assert root.payload["restored"] == 0
        assert root.payload["corrupt"] == (guard in {"corrupt", "score_mismatch"})
        assert root.payload["conflicts"] == (guard == "failed_job")
        assert (
            await session.scalar(
                select(func.count()).select_from(Job).where(Job.dedupe_key.like("reconcile:v1:%"))
            )
            == 0
        )
