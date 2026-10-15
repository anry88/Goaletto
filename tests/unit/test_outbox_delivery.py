"""Delivery state-machine and real transaction orchestration, without a database.

SQL-recording checks generated predicates/DDL; this is not PostgreSQL race testing.
"""

import io
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.schema import CreateTable

from goaletto.application import outbox_delivery as sender
from goaletto.application.match_runtime import queue_notification
from goaletto.application.outbox_delivery import (
    Blocked,
    ChatUnavailable,
    Decision,
    InvalidRequest,
    LeaseLost,
    MessageMissing,
    Outcome,
    RateLimited,
    SendResult,
    TransportTimeout,
    UnknownOutcome,
)
from goaletto.infrastructure.models import (
    AnalyticsManager,
    Club,
    Fixture,
    Manager,
    OutboxBoard,
    OutboxDeliveryAttempt,
    OutboxMessage,
    OwnershipPeriod,
)
from goaletto.infrastructure.outbox_runner import process_one_delivery

NOW = datetime(2026, 10, 18, 18, tzinfo=UTC)


class Rows:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def all(self):
        return self.rows


class Store:
    def __init__(self):
        self.now = NOW
        self.messages = {}
        self.boards = {}
        self.attempts = {}
        self.statements = []
        self.events = []
        self.active = 0
        self.commits = 0
        self.after_commit = None
        self.analytics_id = uuid4()
        self.manager = Manager(
            id=uuid4(), telegram_id=123, reminders_enabled=True, bot_available=True
        )
        self.club = Club(
            id=uuid4(), league_id=uuid4(), name="Club", owner_telegram_id=123, budget=100
        )
        self.ownership = OwnershipPeriod(
            id=uuid4(),
            operation_key="op",
            club_id=self.club.id,
            manager_id=self.manager.id,
            claimed_at=NOW - timedelta(days=10),
            last_game_action_at=NOW - timedelta(days=10),
            expires_at=NOW + timedelta(days=100),
            ended_at=None,
        )
        self.fixture = Fixture(
            id=uuid4(),
            home_club_id=self.club.id,
            away_club_id=uuid4(),
            scheduled_start_at=NOW + timedelta(minutes=5),
            status="live",
        )

    def message(self, kind="match_board", **extra):
        payload = {
            "version": 1,
            "kind": kind,
            "fixture_id": str(self.fixture.id),
            "ownership_id": str(self.ownership.id),
        }
        if kind in {"match_board", "match_result"}:
            payload.update(
                match_id=str(uuid4()),
                tick=3,
                phase="first_half" if kind == "match_board" else "full_time",
                home="Home",
                away="Away",
                home_goals=1,
                away_goals=0,
            )
        if kind == "ownership_reminder":
            payload = {
                "version": 1,
                "kind": kind,
                "ownership_id": str(self.ownership.id),
                "club_id": str(self.club.id),
                "last_action_at": self.ownership.last_game_action_at.isoformat(),
            }
        payload.update(extra)
        row = OutboxMessage(
            id=uuid4(),
            dedupe_key=f"test:{uuid4()}",
            chat_id=123,
            payload=payload,
            due_at=NOW,
            status="pending",
            attempts=0,
            leased_until=None,
            lease_owner=None,
            telegram_message_id=None,
            live_board_key=sender.live_board_key_for(payload),
            priority=sender.priority_for(payload),
            last_error=None,
        )
        self.messages[row.id] = row
        return row

    @asynccontextmanager
    async def transaction(self):
        self.active += 1
        try:
            yield cast(AsyncSession, Session(self))
        finally:
            self.active -= 1
        self.commits += 1
        if self.after_commit is not None:
            self.after_commit(self.commits)


class Session:
    def __init__(self, store):
        self.store = store

    def in_transaction(self):
        return self.store.active > 0

    async def get(self, model, key, **kwargs):
        s = self.store
        if model is OutboxBoard:
            return s.boards.get(key)
        if model is OutboxDeliveryAttempt:
            return s.attempts.get(key)
        lookup = {
            Manager: s.manager,
            Club: s.club,
            OwnershipPeriod: s.ownership,
            Fixture: s.fixture,
        }
        row = lookup.get(model)
        return row if row and row.id == key else None

    async def scalar(self, statement):
        s = self.store
        s.statements.append(statement)
        entity = statement.column_descriptions[0].get("entity")
        params = list(statement.compile().params.values())
        if entity is OutboxMessage:
            return next(
                (s.messages[p] for p in params if isinstance(p, UUID) and p in s.messages), None
            )
        if entity is OutboxBoard:
            return next((s.boards[p] for p in params if isinstance(p, str) and p in s.boards), None)
        if entity is OwnershipPeriod:
            return s.ownership if s.ownership.id in params else None
        if entity is AnalyticsManager:
            return s.analytics_id
        if entity is Manager:
            return s.manager if s.manager.telegram_id in params else None
        raise AssertionError(f"Unexpected select {statement}")

    async def scalars(self, statement):
        s = self.store
        s.statements.append(statement)
        pending = []
        expiring = "ORDER BY outbox_messages.id" in str(statement)
        for m in s.messages.values():
            if expiring and (m.leased_until is None or m.leased_until > s.now):
                continue
            if m.status != "pending" or m.due_at > s.now:
                continue
            if m.leased_until is not None and m.leased_until > s.now:
                continue
            b = s.boards.get(m.live_board_key)
            if (
                not expiring
                and b
                and (b.leased_until and b.leased_until > s.now or b.retry_at and b.retry_at > s.now)
            ):
                continue
            pending.append(m)
        # Use persisted priority, not the derived value: exposes producer bugs.
        pending.sort(key=lambda m: (-m.priority, m.due_at, str(m.id)))
        return Rows(pending[:25])

    async def execute(self, statement):
        s = self.store
        s.statements.append(statement)
        values = statement.compile().params
        if getattr(statement, "is_insert", False) and statement.table.name == "outbox_boards":
            key = values["key"]
            s.boards.setdefault(
                key,
                OutboxBoard(
                    **values,
                    revision=-1,
                    telegram_message_id=None,
                    lease_owner=None,
                    leased_until=None,
                    retry_at=None,
                ),
            )
            return Rows()
        if getattr(statement, "is_update", False):
            if statement.table.name == "outbox_delivery_attempts":
                selected = next((v for v in values.values() if isinstance(v, UUID)), None)
                for attempt in s.attempts.values():
                    row = s.messages[attempt.outbox_id]
                    if (
                        attempt.finished_at is None
                        and row.leased_until is not None
                        and row.leased_until <= s.now
                        and (selected is None or attempt.outbox_id == selected)
                    ):
                        attempt.finished_at = s.now
                        attempt.outcome = "unknown" if attempt.network_started_at else "transient"
                return Rows()
            rows = (
                s.boards.values()
                if statement.table.name == "outbox_boards"
                else s.messages.values()
            )
            count = 0
            for row in rows:
                if row.leased_until is not None and row.leased_until <= s.now:
                    row.leased_until = row.lease_owner = None
                    if isinstance(row, OutboxMessage):
                        row.last_error = "unknown_outcome"
                    count += 1
            result = Rows()
            result.rowcount = count
            return result
        raise AssertionError(f"Unexpected execute {statement}")

    async def flush(self):
        pass

    def add(self, value):
        assert isinstance(value, OutboxDeliveryAttempt)
        self.store.attempts[value.outbox_id, value.attempt] = value


class Transport:
    def __init__(self, store, *, send=None, edit=None):
        self.store, self.send_action, self.edit_action = store, send, edit
        self.calls = []

    async def send_text(self, chat_id, text):
        assert self.store.active == 0, "Network while transaction is open"
        self.calls.append(("send", chat_id, text))
        if isinstance(self.send_action, Exception):
            raise self.send_action
        return self.send_action or SendResult(777)

    async def edit_text(self, chat_id, message_id, text):
        assert self.store.active == 0, "Network while transaction is open"
        self.calls.append(("edit", chat_id, message_id, text))
        if isinstance(self.edit_action, Exception):
            raise self.edit_action


@pytest.fixture
def store(monkeypatch):
    s = Store()

    async def record(session, event):
        assert s.active > 0
        s.events.append(event)
        return True

    monkeypatch.setattr(sender, "record_event", record)
    return s


async def claim(s, row, token="claim"):
    async with s.transaction() as session:
        rows = await sender.claim_outbox(session, s.now, lease_owner=token)
        assert rows == [row]
    return row.attempts


async def finish(s, row, outcome, token="claim", **kwargs):
    async with s.transaction() as session:
        return await sender.finish_delivery(
            session, row.id, s.now, outcome, lease_owner=token, attempt=row.attempts, **kwargs
        )


async def test_runner_commits_before_network_and_records_one_system_effect(store):
    store.fixture.status = "finished"
    row = store.message("match_result")
    previous = store.ownership.last_game_action_at, store.ownership.expires_at
    transport = Transport(store)
    step = await process_one_delivery(store, transport, clock=lambda: store.now)
    assert step.processed and store.commits == 3
    assert row.status == "sent" and row.telegram_message_id == 777
    assert len(transport.calls) == 1
    assert len(store.events) == 1
    event = store.events[0]
    assert event.name == "result_delivered" and event.origin == sender.Origin.SYSTEM
    assert event.session_id is None and event.outbox_id == row.id
    attempt = store.attempts[row.id, 1]
    assert attempt.started_at == attempt.network_started_at == attempt.finished_at == NOW
    assert attempt.outcome == "sent"
    assert previous == (store.ownership.last_game_action_at, store.ownership.expires_at)
    assert not (await process_one_delivery(store, transport, clock=lambda: store.now)).processed
    # A late/repeated failure cannot rewrite successful delivery or duplicate analytics.
    await finish(store, row, Outcome.BLOCKED, token="old-sender")
    assert row.status == "sent" and store.manager.bot_available and len(store.events) == 1


@pytest.mark.parametrize("retry_after", [45, 600, 900, 1800])
async def test_rate_limit_respects_entire_telegram_deadline(store, retry_after):
    row = store.message()
    step = await process_one_delivery(
        store, Transport(store, send=RateLimited(retry_after)), clock=lambda: store.now
    )
    assert row.status == "pending" and row.last_error == "rate_limited"
    assert row.due_at >= NOW + timedelta(seconds=retry_after)
    assert step.retry_after == retry_after
    assert store.boards[row.live_board_key].retry_at == row.due_at
    # New ticks must not bypass this stream's deadline.
    store.message(tick=4)
    async with store.transaction() as session:
        assert not await sender.claim_outbox(session, NOW, lease_owner="another")


@pytest.mark.parametrize(
    "error,outcome",
    [
        (TransportTimeout(), "unknown_outcome"),
        (UnknownOutcome(), "unknown_outcome"),
        (RuntimeError("unsafe token response"), "transient"),
    ],
)
async def test_network_failure_is_bounded_and_never_success(store, error, outcome):
    row = store.message()
    row.attempts = sender.MAX_ATTEMPTS - 1
    await process_one_delivery(store, Transport(store, send=error), clock=lambda: store.now)
    assert row.status == "failed" and row.last_error == outcome
    assert row.telegram_message_id is None and not store.events


async def test_rate_limit_is_also_bounded(store):
    row = store.message()
    row.attempts = sender.MAX_ATTEMPTS - 1
    await process_one_delivery(
        store, Transport(store, send=RateLimited(1800)), clock=lambda: store.now
    )
    assert row.status == "failed" and row.attempts == sender.MAX_ATTEMPTS
    assert store.boards[row.live_board_key].retry_at == NOW + timedelta(seconds=1800)


@pytest.mark.parametrize(
    "error,code", [(Blocked(), "blocked"), (ChatUnavailable(), "chat_unavailable")]
)
async def test_blocked_chat_is_terminal_and_not_user_activity(store, error, code):
    row = store.message()
    await process_one_delivery(store, Transport(store, send=error), clock=lambda: store.now)
    assert row.status == "failed" and row.last_error == code
    assert store.manager.bot_available is False
    assert store.events[0].name == "bot_unavailable" and store.events[0].session_id is None


async def test_invalid_request_does_not_disable_manager(store):
    row = store.message()
    await process_one_delivery(
        store, Transport(store, send=InvalidRequest()), clock=lambda: store.now
    )
    assert row.status == "failed" and row.last_error == "invalid_request"
    assert store.manager.bot_available and not store.events


async def test_duplicate_transport_response_and_analytics_opt_out(store):
    store.fixture.status = "finished"
    store.analytics_id = None
    row = store.message("match_result")
    await process_one_delivery(
        store, Transport(store, send=SendResult(42, duplicate=True)), clock=lambda: store.now
    )
    assert row.status == "sent" and row.telegram_message_id == 42 and not store.events


async def test_live_board_stream_serializes_independent_outbox_rows(store):
    first, second = store.message(tick=1), store.message(tick=2)
    second.due_at += timedelta(seconds=1)
    await claim(store, first)
    store.now += timedelta(seconds=1)
    async with store.transaction() as session:
        assert await sender.claim_outbox(session, store.now, lease_owner="second") == []
    await finish(store, first, Outcome.SENT, telegram_message_id=90)
    await claim(store, second, "second")
    transport = Transport(store)
    async with store.transaction() as session:
        ready = await sender.preflight_delivery(
            session, second.id, store.now, lease_owner="second", attempt=second.attempts
        )
    assert ready[1] == 90
    outcome, telegram_id, _ = await sender.deliver_claimed(
        second, transport, store.now, existing_board_message_id=ready[1]
    )
    assert outcome == Outcome.EDITED and telegram_id == 90
    assert [c[0] for c in transport.calls] == ["edit"]
    await finish(store, second, outcome, "second", telegram_message_id=telegram_id)
    assert store.boards[first.live_board_key].telegram_message_id == 90


async def test_result_sends_new_notification_and_old_retry_cannot_revert_score(store):
    store.message(tick=1)
    await process_one_delivery(store, Transport(store), clock=lambda: store.now)
    store.fixture.status = "finished"
    result = store.message("match_result", tick=30, home_goals=3)
    old = store.message(tick=2)
    transport = Transport(store)
    await process_one_delivery(store, transport, clock=lambda: store.now)
    assert result.status == "sent" and result.live_board_key is None
    assert transport.calls[0][0] == "send"
    await process_one_delivery(store, transport, clock=lambda: store.now)
    assert old.status == "failed" and len(transport.calls) == 1


async def test_delayed_tick_is_superseded_even_when_due_at_moves_after_newer_tick(store):
    newer = store.message(tick=6)
    await process_one_delivery(store, Transport(store), clock=lambda: store.now)
    old = store.message(tick=3)
    old.due_at = NOW + timedelta(minutes=1)
    store.now = old.due_at
    transport = Transport(store)
    await process_one_delivery(store, transport, clock=lambda: store.now)
    assert old.status == "failed" and old.last_error == "superseded" and not transport.calls
    assert store.boards[newer.live_board_key].revision == 60


async def test_deleted_board_is_replaced_but_blocked_edit_never_retries_send(store):
    row = store.message()
    deleted = Transport(store, edit=MessageMissing())
    outcome, telegram_id, _ = await sender.deliver_claimed(
        row, deleted, NOW, existing_board_message_id=12
    )
    assert outcome == Outcome.SENT and telegram_id == 777
    assert [c[0] for c in deleted.calls] == ["edit", "send"]
    for error, expected in [
        (Blocked(), Outcome.BLOCKED),
        (RuntimeError(), Outcome.TRANSIENT),
        (TransportTimeout(), Outcome.UNKNOWN),
    ]:
        transport = Transport(store, edit=error)
        outcome, _, _ = await sender.deliver_claimed(
            row, transport, NOW, existing_board_message_id=12
        )
        assert outcome == expected and [c[0] for c in transport.calls] == ["edit"]


async def test_expired_attempt_and_reused_sender_name_cannot_finish_new_claim(store):
    row = store.message()
    old_attempt = await claim(store, row, "same-sender-name")
    store.now += timedelta(seconds=121)
    async with store.transaction() as session:
        with pytest.raises(LeaseLost):
            await sender.finish_delivery(
                session,
                row.id,
                store.now,
                Outcome.SENT,
                lease_owner="same-sender-name",
                attempt=old_attempt,
                telegram_message_id=12,
            )
        assert await sender.release_expired_leases(session, store.now) == 1
    assert row.last_error == "unknown_outcome"
    await claim(store, row, "same-sender-name")
    async with store.transaction() as session:
        for operation in (sender.finish_delivery, sender.skip_stale):
            args = (
                (Outcome.UNKNOWN,)
                if operation is sender.finish_delivery
                else (Decision.SKIP_STALE,)
            )
            with pytest.raises(LeaseLost):
                await operation(
                    session,
                    row.id,
                    store.now,
                    *args,
                    lease_owner="same-sender-name",
                    attempt=old_attempt,
                )
    assert row.status == "pending" and row.attempts == 2


@pytest.mark.parametrize("change", ["owner", "last_action", "preferences", "available", "expiry"])
async def test_guard_is_rechecked_after_claim_commit(store, change):
    row = store.message("ownership_reminder")

    def changed(commit):
        if commit != 1:
            return
        if change == "owner":
            store.club.owner_telegram_id = 999
        elif change == "last_action":
            store.ownership.last_game_action_at = NOW
        elif change == "preferences":
            store.manager.reminders_enabled = False
        elif change == "available":
            store.manager.bot_available = False
        elif change == "expiry":
            store.ownership.expires_at = NOW

    store.after_commit = changed
    transport = Transport(store)
    await process_one_delivery(store, transport, clock=lambda: store.now)
    assert row.status == "failed" and not transport.calls


async def test_stale_fixture_reminder_and_foreign_fixture_never_send(store):
    row = store.message("fixture_reminder")
    transport = Transport(store)
    await process_one_delivery(store, transport, clock=lambda: store.now)
    assert row.status == "failed" and not transport.calls
    row = store.message()
    store.fixture.home_club_id = uuid4()
    await process_one_delivery(store, transport, clock=lambda: store.now)
    assert row.status == "failed" and not transport.calls


@pytest.mark.parametrize(
    "extra", [{"version": True}, {"kind": []}, {"tick": "3"}, {"phase": "oops"}]
)
async def test_malformed_payload_is_quarantined(store, extra):
    row = store.message()
    row.payload = dict(row.payload, **extra)
    transport = Transport(store)
    await process_one_delivery(store, transport, clock=lambda: store.now)
    assert row.status == "failed" and not transport.calls


async def test_claim_prioritizes_persisted_result_and_compiles_stream_exclusion(store):
    store.fixture.status = "finished"
    low = store.message("ownership_reminder")
    high = store.message("match_result")
    async with store.transaction() as session:
        assert await sender.claim_outbox(session, NOW, lease_owner="x") == [high]
    sql = str(store.statements[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql and "ORDER BY outbox_messages.priority DESC" in sql
    assert "EXISTS" in sql and "outbox_boards.retry_at" in sql
    assert low.attempts == 0


async def test_actual_runtime_producer_writes_result_priority_without_live_board_key(store):
    class ProducerRecorder:
        def __init__(self):
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            from goaletto.infrastructure.models import MatchStage, MatchTick

            if hasattr(statement, "column_descriptions") and statement.column_descriptions[0][
                "entity"
            ] in {MatchTick, MatchStage}:
                return Rows([])
            return Rows([(store.ownership, store.manager)])

    from goaletto.application.match_simulation import demo_match, simulate_match

    state, _ = simulate_match(demo_match())
    recorder = ProducerRecorder()
    await queue_notification(
        cast(AsyncSession, recorder), store.fixture, "match_result", "finished", NOW, state
    )
    params = recorder.statements[-1].compile().params
    assert params["priority"] == 100
    assert params["live_board_key"] is None
    assert sender.valid_payload(params["payload"])
    text = sender.render_text(params["payload"])
    assert "Финальный свисток" in text


def test_render_escapes_html_and_uses_game_minutes_and_abandoned_status(store):
    row = store.message(home="<b>A & B</b>", away="Club")
    text = sender.render_text(row.payload)
    assert "9′" in text and "&lt;b&gt;A &amp; B&lt;/b&gt;" in text and "<b>" not in text
    payload = dict(row.payload, kind="match_result", phase="abandoned")
    assert "Итоговый результат не присуждён" in sender.render_text(payload)


async def test_transactions_and_lease_bounds_are_required(store):
    session = cast(AsyncSession, Session(store))
    with pytest.raises(ValueError, match="transaction"):
        await sender.claim_outbox(session, NOW, lease_owner="x")
    async with store.transaction() as session:
        for seconds in (0, -1, float("nan"), 301):
            with pytest.raises(ValueError, match="Lease"):
                await sender.claim_outbox(session, NOW, lease_owner="x", lease_seconds=seconds)
    assert sender.compute_backoff(8) == 600
    with pytest.raises(ValueError):
        sender.compute_backoff(0)
    with pytest.raises(ValueError):
        sender.compute_backoff(1, float("nan"))


def test_frozen_migration_and_downgrade_match_board_ddl(monkeypatch):
    monkeypatch.setenv("GOALETTO_DATABASE_URL", "postgresql+asyncpg://offline.invalid/goaletto")
    output = io.StringIO()
    command.upgrade(Config("alembic.ini", output_buffer=output), "head", sql=True)
    sql = output.getvalue()

    def normalized(value):
        return " ".join(value.split()).rstrip(";")

    assert normalized(
        str(CreateTable(OutboxBoard.__table__).compile(dialect=postgresql.dialect()))
    ) in normalized(sql)
    assert normalized(
        str(CreateTable(OutboxDeliveryAttempt.__table__).compile(dialect=postgresql.dialect()))
    ) in normalized(sql)
    assert "ADD COLUMN leased_until" in sql and "ck_outbox_lease" in sql
    assert "UPDATE outbox_messages SET priority" in sql
    down = io.StringIO()
    command.downgrade(
        Config("alembic.ini", output_buffer=down), "0009_outbox_delivery:0008_club_entry", sql=True
    )
    assert "DROP TABLE outbox_boards" in down.getvalue()
    assert "Reconcile leased/delivered" in down.getvalue()


async def test_new_inbound_confirmation_wins_over_old_blocked_response(store):
    row = store.message()

    class ReturningTransport(Transport):
        async def send_text(self, chat_id, text):
            assert store.active == 0
            store.now += timedelta(seconds=1)
            store.manager.bot_available = True
            store.manager.bot_confirmed_at = store.now
            raise Blocked()

    await process_one_delivery(store, ReturningTransport(store), clock=lambda: store.now)
    assert row.status == "failed" and store.manager.bot_available
    assert store.attempts[row.id, 1].outcome == "blocked"


async def test_crash_after_network_preflight_is_recorded_unknown_on_reclaim(store):
    row = store.message()
    await claim(store, row, "old")
    async with store.transaction() as session:
        await sender.preflight_delivery(session, row.id, store.now, lease_owner="old", attempt=1)
    assert store.attempts[row.id, 1].outcome == "sending"
    store.now += timedelta(seconds=121)
    await claim(store, row, "new")
    assert store.attempts[row.id, 1].outcome == "unknown"
    assert store.attempts[row.id, 1].finished_at == store.now
    assert store.attempts[row.id, 2].outcome == "claimed"


async def test_sweeper_does_not_clear_another_board_claim(store):
    row = store.message()
    await claim(store, row, "old")
    board = store.boards[row.live_board_key]
    store.now += timedelta(seconds=121)
    board.lease_owner = "new"
    board.leased_until = store.now + timedelta(seconds=120)
    # The expired old row is excluded by the regular claim, but the sweeper
    # closes its old attempt without touching the newer stream lease.
    async with store.transaction() as session:
        assert await sender.release_expired_leases(session, store.now) == 1
    assert board.lease_owner == "new" and row.lease_owner is None


async def test_runner_bounds_entire_edit_send_exchange_and_persists_timeout(store, monkeypatch):
    import asyncio

    from goaletto.infrastructure import outbox_runner

    real_timeout = asyncio.timeout
    monkeypatch.setattr(outbox_runner.asyncio, "timeout", lambda seconds: real_timeout(0.001))

    class SlowTransport(Transport):
        async def send_text(self, chat_id, text):
            assert store.active == 0
            await asyncio.sleep(10)
            return SendResult(999)

    row = store.message()
    await process_one_delivery(store, SlowTransport(store), clock=lambda: store.now)
    assert row.status == "pending" and row.last_error == "unknown_outcome"
    assert store.attempts[row.id, 1].outcome == "unknown"


async def test_result_is_sent_even_with_existing_board_and_reminders_disabled(store):
    store.fixture.status = "finished"
    store.manager.reminders_enabled = False
    row = store.message("match_result")
    transport = Transport(store)
    outcome, message_id, _ = await sender.deliver_claimed(
        row, transport, NOW, existing_board_message_id=12345
    )
    assert outcome == Outcome.SENT and message_id == 777
    assert [call[0] for call in transport.calls] == ["send"]
    async with store.transaction() as session:
        assert await sender.check_recipient(session, row, NOW) == Decision.DELIVER
    await process_one_delivery(store, transport, clock=lambda: store.now)
    count = len(transport.calls)
    await process_one_delivery(store, transport, clock=lambda: store.now)
    assert len(transport.calls) == count  # Terminal outbox receipt suppresses retries.


def test_knockout_result_keeps_match_goals_separate_from_aggregate_and_penalties(store):
    row = store.message(
        "match_result",
        tick=40,
        phase="full_time",
        game_minute=120,
        home_goals=0,
        away_goals=0,
        aggregate=[2, 2],
        penalties=[5, 4],
        penalty_kicks=12,
        tie_winner_name="Home <club>",
    )
    text = sender.render_text(row.payload)
    assert "Home 0:0 Away" in text
    assert "2:2" in text and "5:4" in text
    assert "Home &lt;club&gt;" in text
    pending = {**row.payload, "kind": "match_board", "phase": "penalties", "penalty_kicks": 11}
    assert sender.board_revision(row.payload) > sender.board_revision(pending)
    assert sender.valid_payload(pending)
