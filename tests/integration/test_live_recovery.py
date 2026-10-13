"""Real PostgreSQL rollback/race/window recovery without catch-up."""

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from goaletto.application.live_commands import load_live_screen
from goaletto.application.match_runtime import handle_calendar_job, pause_match, resume_match
from goaletto.application.match_simulation import simulate_match
from goaletto.application.outbox_delivery import Decision, check_recipient, render_text
from goaletto.application.results import finalize_result
from goaletto.domain.match_codec import restore_state, state_hash
from goaletto.infrastructure.jobs import process_one_job
from goaletto.infrastructure.models import (
    AcceptedCommand,
    Job,
    Match,
    MatchRecovery,
    MatchResult,
    MatchTick,
    OutboxMessage,
    OwnershipPeriod,
)
from goaletto.presentation.i18n import using_locale
from tests.integration.test_agent_wave import job
from tests.integration.test_live_commands import accept, live

pytestmark = pytest.mark.integration


async def tick_job(session, fixture_id, tick):
    return await session.scalar(
        select(Job).where(
            Job.kind == "match.tick",
            Job.payload["fixture_id"].astext == str(fixture_id),
            Job.payload["tick_number"].as_integer() == tick,
        )
    )


async def test_lag_restores_window_and_commands_then_replay_finishes_once(sessions):
    actor, fixture_id, start, screen = await live(sessions)
    _, first = await accept(
        sessions,
        actor,
        screen,
        "mentality",
        {"mentality": "attack"},
        at=start + timedelta(seconds=20),
    )
    discovery = start + timedelta(minutes=6)
    async with sessions.begin() as session:
        match = await session.get(Match, screen.match_id)
        initial_hash = state_hash(restore_state(match.state))
        old_deadline = match.next_due_at
        owner = await session.get(OwnershipPeriod, screen.ownership_id)
        activity = owner.last_game_action_at
        # Isolate the selected fixture; other calendar/lease jobs remain held.
        await session.execute(update(Job).where(Job.status == "pending").values(status="held"))
        for tick in (1, 2):
            row = await tick_job(session, fixture_id, tick)
            row.status = "pending"
    async with sessions.begin() as session:
        assert await process_one_job(session, at=discovery)
    async with sessions.begin() as session:
        match = await session.get(Match, screen.match_id)
        deadline = discovery + timedelta(seconds=60)
        assert match.next_due_at == deadline and match.completed_tick == 0
        assert state_hash(restore_state(match.state)) == initial_hash
        assert (await session.get(AcceptedCommand, first.id)).status == "accepted"
        assert (await tick_job(session, fixture_id, 1)).status == "pending"
        assert (await tick_job(session, fixture_id, 2)).due_at == deadline + timedelta(seconds=60)
        assert not await process_one_job(session, at=discovery)
        assert await session.scalar(select(func.count()).select_from(MatchRecovery)) == 1
        recovery = await session.scalar(select(MatchRecovery))
        assert recovery.previous_due_at == old_deadline and recovery.state_hash == initial_hash
        notice = await session.scalar(
            select(OutboxMessage).where(OutboxMessage.payload["kind"].astext == "match_recovery")
        )
        assert await check_recipient(session, notice, discovery) == Decision.DELIVER
        with using_locale("en"):
            assert "will resume" in render_text(notice.payload)
        owner = await session.get(OwnershipPeriod, screen.ownership_id)
        assert owner.last_game_action_at == activity
        refreshed = await load_live_screen(
            session, replace(actor, at=discovery), clock=lambda: discovery, record=False
        )
        assert refreshed.open and refreshed.target == 1 and refreshed.deadline == deadline
    _, second = await accept(
        sessions,
        actor,
        refreshed,
        "tactics",
        {"tactic": "pressing"},
        update=101,
        at=discovery + timedelta(seconds=15),
    )
    async with sessions.begin() as session:
        assert await process_one_job(session, at=deadline)
        assert not await process_one_job(session, at=deadline)
    async with sessions.begin() as session:
        match = await session.get(Match, screen.match_id)
        assert match.completed_tick == 1
        expected, _ = simulate_match(restore_state(match.state))
        for command_id in (first.id, second.id):
            assert (await session.get(AcceptedCommand, command_id)).status == "applied"
        assert await check_recipient(session, notice, deadline) == Decision.SKIP_STALE
    for tick in range(2, 31):
        if tick == 16:
            await job(sessions, "match.second_half_start", fixture_id)
        await job(sessions, "match.tick", fixture_id, tick)
    await job(sessions, "match.tick", fixture_id, 30)
    async with sessions.begin() as session:
        assert not await finalize_result(session, fixture_id, start + timedelta(minutes=60))
        assert not await finalize_result(session, fixture_id, start + timedelta(minutes=60))
    async with sessions.begin() as session:
        match = await session.get(Match, screen.match_id)
        assert state_hash(restore_state(match.state)) == state_hash(expected)
        assert await session.scalar(select(func.count()).select_from(MatchTick)) == 30
        assert await session.scalar(select(func.count()).select_from(MatchResult)) == 1


async def test_recovery_rollback_and_two_workers_create_one_incident(sessions):
    _, fixture_id, start, screen = await live(sessions)
    discovery = start + timedelta(minutes=5)
    async with sessions.begin() as session:
        await session.execute(update(Job).where(Job.status == "pending").values(status="held"))
        row = await tick_job(session, fixture_id, 1)
        row.status = "pending"
        original = row.due_at
    async with sessions() as session:
        assert await process_one_job(session, at=discovery)
        await session.rollback()
    async with sessions.begin() as session:
        assert (await tick_job(session, fixture_id, 1)).due_at == original
        assert (await session.get(Match, screen.match_id)).paused_seconds == 0
        assert await session.scalar(select(func.count()).select_from(MatchRecovery)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutboxMessage)
                .where(OutboxMessage.payload["kind"].astext == "match_recovery")
            )
            == 0
        )

    async def worker():
        async with sessions.begin() as session:
            return await process_one_job(session, at=discovery)

    assert sum(await asyncio.gather(worker(), worker())) == 1
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(MatchRecovery)) == 1
        assert (await session.get(Match, screen.match_id)).completed_tick == 0


async def test_halftime_lag_and_late_manual_resume_preserve_full_windows(sessions):
    actor, fixture_id, start, screen = await live(sessions)
    for tick in range(1, 16):
        await job(sessions, "match.tick", fixture_id, tick)
    discovery = start + timedelta(minutes=30)
    async with sessions.begin() as session:
        row = await session.scalar(
            select(Job).where(
                Job.kind == "match.second_half_start",
                Job.payload["fixture_id"].astext == str(fixture_id),
            )
        )
        assert await handle_calendar_job(session, row, discovery) == "rescheduled"
        match = await session.get(Match, screen.match_id)
        assert match.phase == "half_time" and match.completed_tick == 15
        assert match.next_due_at == discovery + timedelta(minutes=3)
        refreshed = await load_live_screen(
            session, replace(actor, at=discovery), clock=lambda: discovery, record=False
        )
        assert refreshed.open and refreshed.halftime
    await accept(
        sessions,
        actor,
        refreshed,
        "mentality",
        {"mentality": "attack"},
        at=discovery + timedelta(seconds=30),
    )
    await job(sessions, "match.second_half_start", fixture_id)
    async with sessions.begin() as session:
        match = await session.get(Match, screen.match_id)
        before = state_hash(restore_state(match.state))
        expired = match.next_due_at + timedelta(minutes=3)
        await pause_match(session, fixture_id, expired)
    resume = expired + timedelta(minutes=5)
    async with sessions.begin() as session:
        await resume_match(session, fixture_id, resume)
        await resume_match(session, fixture_id, resume)  # duplicate operator request
        match = await session.get(Match, screen.match_id)
        assert match.phase == "second_half" and match.completed_tick == 15
        assert match.next_due_at == resume + timedelta(seconds=60)
        assert state_hash(restore_state(match.state)) == before
        assert await session.scalar(select(func.count()).select_from(MatchRecovery)) == 2
