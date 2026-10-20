"""Settings router transaction boundaries. Fake database and Telegram, no network."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from goaletto.application.game_actions import Actor
from goaletto.bot import settings_router
from goaletto.bot.settings_views import disable_callback
from goaletto.domain.ownership import ownership_deadline
from goaletto.infrastructure.models import (
    Club,
    GameActionReceipt,
    Manager,
    OwnershipPeriod,
)

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)


class FakeSession:
    def __init__(self, db: "FakeDatabase") -> None:
        self.db = db

    def in_transaction(self) -> bool:
        return self.db.depth > 0

    async def scalar(self, statement: object, params: object = None) -> object | None:
        if isinstance(statement, TextClause):
            return True
        descriptions = getattr(statement, "column_descriptions", None)
        entity = descriptions[0].get("entity") if descriptions else None
        if entity is Manager:
            return self.db.manager
        if entity is Club:
            return self.db.club
        if entity is OwnershipPeriod:
            values = list(statement.compile().params.values())  # type: ignore[union-attr]
            if self.db.ownership.id in values:
                return self.db.ownership
            return self.db.ownership
        # AnalyticsManager lookup for actor resolution: opt-out.
        return None

    async def get(self, model: object, key: object, **kwargs: object) -> object | None:
        if model is Manager:
            return self.db.manager if key == self.db.manager.id else None
        if model is GameActionReceipt:
            return self.db.receipts.get(cast(tuple[object, object], key))
        return None

    async def execute(self, statement: object, params: object = None) -> None:
        return None

    def add(self, value: object) -> None:
        assert isinstance(value, GameActionReceipt)
        self.db.receipts[(value.operation_id, value.effect_number)] = value

    async def flush(self) -> None:
        return None


class FakeDatabase:
    def __init__(self) -> None:
        self.depth = 0
        self.manager = Manager(
            timezone_name="UTC",
            id=uuid4(),
            telegram_id=123,
            reminders_enabled=True,
            bot_available=True,
        )
        self.club = Club(
            id=uuid4(), league_id=uuid4(), name="Club", owner_telegram_id=123, budget=100
        )
        last = NOW - timedelta(days=1)
        self.ownership = OwnershipPeriod(
            id=uuid4(),
            operation_key="op",
            manager_id=self.manager.id,
            club_id=self.club.id,
            claimed_at=last,
            last_game_action_at=last,
            expires_at=ownership_deadline(last),
            ended_at=None,
            end_reason=None,
        )
        self.receipts: dict[tuple[object, object], GameActionReceipt] = {}

    @asynccontextmanager
    async def transaction(self):  # type: ignore[no-untyped-def]
        assert self.depth == 0
        self.depth += 1
        try:
            yield cast(AsyncSession, FakeSession(self))
        finally:
            self.depth -= 1


class FakeMessage:
    photo = None

    def __init__(self, db: FakeDatabase) -> None:
        self.db = db
        self.from_user = SimpleNamespace(id=123)
        self.chat = SimpleNamespace(type="private")
        self.answers: list[tuple[str, object]] = []
        self.edits: list[tuple[str, object]] = []

    async def answer(self, text: str, reply_markup: object = None) -> None:
        assert self.db.depth == 0, "Telegram send while transaction is open"
        self.answers.append((text, reply_markup))

    async def edit_text(self, text: str, reply_markup: object = None) -> None:
        assert self.db.depth == 0, "Telegram edit while transaction is open"
        self.edits.append((text, reply_markup))


class FakeQuery:
    def __init__(self, db: FakeDatabase, message: FakeMessage, data: str, qid: str) -> None:
        self.db = db
        self.message = message
        self.data = data
        self.id = qid
        self.from_user = SimpleNamespace(id=123)
        self.answered = False

    async def answer(self) -> None:
        assert self.db.depth == 0, "Callback answer while transaction is open"
        self.answered = True


async def test_settings_command_answers_after_commit() -> None:
    db = FakeDatabase()
    message = FakeMessage(db)
    update = SimpleNamespace(update_id=7)
    await settings_router.settings_command(
        cast(object, message), cast(object, db), cast(object, update)
    )  # type: ignore[arg-type]
    assert len(message.answers) == 1 and "Настройки" in message.answers[0][0]


async def test_retired_disable_button_never_changes_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_router, "Message", object)
    db = FakeDatabase()
    message = FakeMessage(db)
    data = disable_callback(db.ownership.id)
    first = FakeQuery(db, message, data, "callback-1")
    await settings_router.settings_callback(cast(object, first), cast(object, db))  # type: ignore[arg-type]
    assert first.answered and db.manager.reminders_enabled is True
    assert len(message.edits) == 1 and "/settings" in message.edits[0][0]
    # Same explicit callback delivered again: still disabled, never toggled back.
    second = FakeQuery(db, message, data, "callback-1")
    await settings_router.settings_callback(cast(object, second), cast(object, db))  # type: ignore[arg-type]
    assert db.manager.reminders_enabled is True
    assert len(message.edits) == 2 and "/settings" in message.edits[1][0]
    # Ownership deadline was preserved by the mutation path.
    assert db.ownership.expires_at == ownership_deadline(db.ownership.last_game_action_at)
    assert db.manager.bot_available is True


async def test_actor_mismatch_reports_stale_button(monkeypatch: pytest.MonkeyPatch) -> None:
    _ = Actor  # keep import used for type parity with application guards
    monkeypatch.setattr(settings_router, "Message", object)
    db = FakeDatabase()
    db.manager.telegram_id = 999
    message = FakeMessage(db)
    query = FakeQuery(db, message, disable_callback(db.ownership.id), "callback-9")
    await settings_router.settings_callback(cast(object, query), cast(object, db))  # type: ignore[arg-type]
    assert len(message.edits) == 1 and "/settings" in message.edits[0][0]


@pytest.fixture(autouse=True)
def isolate_cosmetic_storage(monkeypatch):
    # These units exercise preference/IO boundaries; cosmetic persistence has PG coverage.
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from goaletto.application import academy, cosmetics

    for name in ("ensure_initial", "ensure_vip_bonus", "available_grant", "vip_payment"):
        monkeypatch.setattr(cosmetics, name, AsyncMock(return_value=None))
    monkeypatch.setattr(
        academy,
        "current_season",
        AsyncMock(return_value=SimpleNamespace(status="active", cycle_epoch=NOW.date())),
    )
