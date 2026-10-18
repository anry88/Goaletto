"""Durable measured stages, unknown sends and hand-counted PostgreSQL percentiles."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from goaletto.application.live_commands import accept_live_command
from goaletto.bot.live_views import render
from goaletto.infrastructure.models import AcceptedCommand, RuntimeRequest
from goaletto.infrastructure.request_timing import RequestTiming, observe_request
from goaletto.infrastructure.runtime_observation import report
from tests.integration.test_agent_wave import job
from tests.integration.test_live_commands import live

pytestmark = pytest.mark.integration


class Source:
    def __init__(self, sessions):
        self.sessions = sessions

    @asynccontextmanager
    async def transaction(self):
        async with self.sessions.begin() as session:
            yield session


async def test_command_commit_timeout_restart_and_retry_have_separate_durable_attempts(
    sessions, monkeypatch
):
    actor, fixture, at, screen = await live(sessions)
    actor = replace(actor, operation_id=uuid4(), update_id=100)
    instant = at

    def clock():
        nonlocal instant
        instant += timedelta(milliseconds=10)
        return instant

    monkeypatch.setattr("goaletto.infrastructure.request_timing.now", clock)
    source = Source(sessions)

    async def timeout():
        raise TimeoutError("do not export network errors")

    async def sent():
        # The accepted command and network intent are visible on a separate
        # connection while the fake Telegram call runs, with no held game tx.
        async with sessions.begin() as session:
            assert await session.scalar(select(func.count()).select_from(AcceptedCommand)) == 1
        return None

    async def command(timing):
        async with timing.transaction() as session:
            accepted = await accept_live_command(
                session,
                actor,
                match_id=screen.match_id,
                ownership_id=screen.ownership_id,
                target=1,
                halftime=False,
                kind="mentality",
                payload={"mentality": "attack"},
                clock=lambda: at,
            )
            timing.bind(fixture, accepted.id)
        timing.render(render, screen)
        return accepted

    with pytest.raises(TimeoutError):
        async with observe_request(source, actor.operation_id, "match_callback") as timing:
            first = await command(timing)
            await timing.send(timeout)
    # Simulate a process dying after committing send intent. A restart must not
    # manufacture a success or reuse this observation as the retry attempt.
    crashed = RequestTiming(source, actor.operation_id, "match_callback")
    await crashed.save()
    await command(crashed)
    crashed.values.update(network_started_at=clock(), delivery_outcome="sending")
    await crashed.save()
    async with observe_request(source, actor.operation_id, "match_callback") as timing:
        again = await command(timing)
        assert again.id == first.id
        await timing.send(sent)
    await job(sessions, "match.tick", fixture, 1)
    async with sessions.begin() as session:
        rows = (
            await session.scalars(select(RuntimeRequest).order_by(RuntimeRequest.received_at))
        ).all()
        assert len(rows) == 3
        assert [r.delivery_outcome for r in rows] == ["unknown", "sending", "accepted"]
        assert rows[1].finished_at is None
        assert all(r.command_id == first.id and r.fixture_id == fixture for r in rows)
        assert all(
            r.committed_at < r.render_started_at <= r.rendered_at < r.network_started_at
            for r in rows
        )
        assert (await session.get(AcceptedCommand, first.id)).status == "applied"
        data = await report(session, at, at + timedelta(minutes=2))
        states = {r["delivery_outcome"]: r["attempts"] for r in data["request_outcomes"]}
        assert states == {"accepted": 1, "unknown": 2}
        metrics = {r["metric"]: r for r in data["metrics"]}
        assert metrics["command_wait_for_tick"]["measured"] == 1
        assert metrics["request_telegram"]["unknown"] == 1
        assert data["tick_wait_is_technical_latency"] is False


async def test_sql_percentiles_missing_observation_and_asof_are_truthful(sessions):
    from tests.integration.test_agent_wave import NOW

    at = NOW
    async with sessions.begin() as session:
        from goaletto.infrastructure.models import (
            Job,
            OperationAttempt,
            OutboxDeliveryAttempt,
            OutboxMessage,
        )

        worker = Job(
            id=uuid4(),
            dedupe_key="measured-worker",
            kind="system.ping",
            payload={},
            due_at=at,
            status="done",
            attempts=3,
        )
        session.add(worker)
        await session.flush()
        for retry, stage, lateness in (
            (1, "match.tick", 1),
            (2, "match.tick", 3),
            (3, "world.catch_up:match.tick", 1000),
        ):
            session.add(
                OperationAttempt(
                    id=uuid4(),
                    job_id=worker.id,
                    operation_id=uuid4(),
                    retry_number=retry,
                    stage=stage,
                    started_at=at + timedelta(seconds=4),
                    finished_at=at + timedelta(seconds=5),
                    due_at=at + timedelta(seconds=4 - lateness),
                    outcome="committed",
                )
            )
        message = OutboxMessage(
            id=uuid4(),
            dedupe_key="measured-outbox",
            chat_id=1001,
            payload={},
            due_at=at + timedelta(seconds=2),
            enqueued_at=at,
            status="sent",
            attempts=1,
        )
        session.add(message)
        await session.flush()
        session.add(
            OutboxDeliveryAttempt(
                outbox_id=message.id,
                attempt=1,
                lease_owner="measured",
                started_at=at + timedelta(seconds=3),
                available_at=at + timedelta(seconds=2),
                render_started_at=at + timedelta(seconds=3.1),
                rendered_at=at + timedelta(seconds=3.2),
                network_started_at=at + timedelta(seconds=4),
                finished_at=at + timedelta(seconds=5),
                outcome="sent",
            )
        )
        for seconds in (1, 3):
            session.add(
                RuntimeRequest(
                    id=uuid4(),
                    operation_id=uuid4(),
                    route="match_open",
                    received_at=at,
                    committed_at=at + timedelta(seconds=seconds),
                    render_started_at=at + timedelta(seconds=4),
                    rendered_at=at + timedelta(seconds=5),
                    network_started_at=at + timedelta(seconds=6),
                    finished_at=at + timedelta(seconds=7),
                    business_outcome="committed",
                    delivery_outcome="accepted",
                )
            )
        session.add(
            RuntimeRequest(
                id=uuid4(),
                operation_id=uuid4(),
                route="history",
                received_at=at,
                business_outcome="unknown",
                delivery_outcome="not_started",
            )
        )
    async with sessions.begin() as session:
        data = await report(session, at, at + timedelta(seconds=10))
        metrics = {r["metric"]: r for r in data["metrics"]}
        assert metrics["request_commit"]["measured"] == 2
        assert metrics["request_commit"]["unknown"] == 1
        assert metrics["request_commit"]["p50_seconds"] == 2
        assert metrics["request_commit"]["p95_seconds"] == pytest.approx(2.9)
        assert metrics["worker_lateness"]["measured"] == 2
        assert metrics["worker_lateness"]["p50_seconds"] == 2
        assert metrics["worker_lateness"]["p95_seconds"] == pytest.approx(2.9)
        assert metrics["delivery_enqueue_to_claim"]["p50_seconds"] == 1
        assert metrics["delivery_render"]["p50_seconds"] == pytest.approx(0.1)
        earlier = await report(session, at, at + timedelta(seconds=2))
        commit = next(r for r in earlier["metrics"] if r["metric"] == "request_commit")
        assert commit["measured"] == 1 and commit["unknown"] == 2
        assert "telegram_id" not in str(data) and "operation_id" not in str(data)


async def test_real_dispatcher_command_ack_commit_render_and_send_are_correlated(
    sessions, monkeypatch
):
    from datetime import datetime

    from aiogram import Bot
    from aiogram.methods import AnswerCallbackQuery
    from aiogram.types import CallbackQuery, Chat, Message, Update, User

    from goaletto.bot.live_views import callback
    from goaletto.bot.main import build_dispatcher
    from goaletto.domain.match_types import Mentality
    from tests.integration.test_game_dispatcher import Source as RoutedSource
    from tests.integration.test_game_dispatcher import Telegram

    actor, fixture, at, screen = await live(sessions)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at + timedelta(seconds=10)

    monkeypatch.setattr("goaletto.bot.live_router.datetime", Clock)
    monkeypatch.setattr("goaletto.application.live_commands.datetime", Clock)
    source = RoutedSource(sessions)

    class CheckedTelegram(Telegram):
        async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109 - aiogram API
            async with sessions.begin() as session:
                requests = (
                    await session.scalars(
                        select(RuntimeRequest).order_by(RuntimeRequest.received_at)
                    )
                ).all()
                if not isinstance(method, AnswerCallbackQuery):
                    assert requests[-1].business_outcome == "committed"
                    assert requests[-1].delivery_outcome == "sending"
                    assert requests[-1].command_id is not None
            return await super().make_request(bot, method, timeout)

    telegram = CheckedTelegram(source)
    bot = Bot("123456:FAKE_LOCAL_TEST_TOKEN", session=telegram)
    dispatcher = build_dispatcher(source, game_enabled=True)
    user = User(id=actor.telegram_id, is_bot=False, first_name="Manager")
    incoming = Update(
        update_id=7001,
        callback_query=CallbackQuery(
            id="latency-live-callback",
            from_user=user,
            chat_instance="local",
            message=Message(message_id=1, date=at, chat=Chat(id=user.id, type="private")),
            data=callback(screen, "m", list(Mentality).index(Mentality.ATTACK)),
        ),
    )
    try:
        await dispatcher.feed_update(bot, incoming)
        await dispatcher.feed_update(bot, incoming)
    finally:
        await bot.session.close()
    assert isinstance(telegram.calls[0], AnswerCallbackQuery)
    async with sessions.begin() as session:
        requests = (
            await session.scalars(select(RuntimeRequest).order_by(RuntimeRequest.received_at))
        ).all()
        assert len(requests) == 2
        assert requests[0].operation_id == requests[1].operation_id
        assert requests[0].command_id == requests[1].command_id
        assert all(r.delivery_outcome == "accepted" and r.fixture_id == fixture for r in requests)
        assert all(
            r.committed_at
            <= r.render_started_at
            <= r.rendered_at
            <= r.network_started_at
            <= r.finished_at
            for r in requests
        )
        assert await session.scalar(select(func.count()).select_from(AcceptedCommand)) == 1


async def test_real_outbox_runner_commits_render_and_fresh_send_intent_before_network(sessions):
    from goaletto.application.outbox_delivery import SendResult
    from goaletto.infrastructure.models import OutboxDeliveryAttempt, OutboxMessage, OwnershipPeriod
    from goaletto.infrastructure.outbox_runner import process_one_delivery
    from tests.integration.test_game_dispatcher import Source as RoutedSource

    _, _, at, screen = await live(sessions)
    source = RoutedSource(sessions)
    instant = at

    def clock():
        nonlocal instant
        instant += timedelta(milliseconds=10)
        return instant

    async with sessions.begin() as session:
        action = (await session.get(OwnershipPeriod, screen.ownership_id)).last_game_action_at

    class Transport:
        async def send_text(self, chat_id, text):
            assert source.depth == 0
            async with sessions.begin() as session:
                observed = await session.scalar(select(OutboxDeliveryAttempt))
                assert observed.outcome == "sending"
                assert (
                    observed.started_at
                    <= observed.render_started_at
                    <= observed.rendered_at
                    <= observed.network_started_at
                )
            return SendResult(55)

        async def edit_text(self, *args):
            raise AssertionError("Initial board must be sent once")

    assert (await process_one_delivery(source, Transport(), clock=clock)).processed
    async with sessions.begin() as session:
        observed = await session.scalar(select(OutboxDeliveryAttempt))
        row = await session.get(OutboxMessage, observed.outbox_id)
        assert row.enqueued_at is not None and observed.available_at == at
        assert observed.outcome == "sent" and observed.finished_at >= observed.network_started_at
        assert (
            await session.get(OwnershipPeriod, screen.ownership_id)
        ).last_game_action_at == action
