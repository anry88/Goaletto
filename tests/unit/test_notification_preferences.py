"""Notification preference use cases. SQL recording, no database, no network."""

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from goaletto.application.game_actions import Actor, PermissionDenied
from goaletto.application.notification_preferences import (
    SETTINGS_KIND,
    get_settings,
    set_reminders_enabled,
)
from goaletto.application.outbox_delivery import Decision, check_recipient
from goaletto.domain.ownership import ownership_deadline
from goaletto.infrastructure.models import (
    Club,
    GameActionReceipt,
    Manager,
    OutboxMessage,
    OwnershipPeriod,
)

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)


class FakeSession:
    """Minimal recording session: advisory locks succeed, selects resolve locally."""

    def __init__(self) -> None:
        self.manager = Manager(
            id=uuid4(), telegram_id=123, reminders_enabled=True, bot_available=True
        )
        self.club = Club(
            id=uuid4(), league_id=uuid4(), name="Club", owner_telegram_id=123, budget=100
        )
        claimed = NOW - timedelta(days=10)
        last = NOW - timedelta(days=1)
        self.ownership = OwnershipPeriod(
            id=uuid4(),
            operation_key="op-current",
            manager_id=self.manager.id,
            club_id=self.club.id,
            claimed_at=claimed,
            last_game_action_at=last,
            expires_at=ownership_deadline(last),
            ended_at=None,
            end_reason=None,
        )
        self.old_ownership: OwnershipPeriod | None = None
        self.actor = Actor(self.manager.id, 123, None, uuid4(), NOW)
        self.receipts: dict[tuple[object, object], GameActionReceipt] = {}
        self.statements: list[object] = []
        self.job_inserts: list[dict[str, object]] = []
        self.record_events_called = 0

    def in_transaction(self) -> bool:
        return True

    async def scalar(self, statement: object, params: object = None) -> object | None:
        self.statements.append(statement)
        if isinstance(statement, TextClause):
            return True
        descriptions = getattr(statement, "column_descriptions", None)
        entity = descriptions[0].get("entity") if descriptions else None
        if entity is Manager:
            return self.manager
        if entity is Club:
            return self.club
        if entity is OwnershipPeriod:
            values = list(statement.compile().params.values())  # type: ignore[union-attr]
            candidates = [self.ownership]
            if self.old_ownership is not None:
                candidates.append(self.old_ownership)
            for candidate in candidates:
                if candidate.id in values:
                    return candidate
            # Active-ownership query by manager: return the current period.
            return self.ownership
        return None

    async def get(self, model: object, key: object, **kwargs: object) -> object | None:
        if model is Manager:
            return self.manager if key == self.manager.id else None
        if model is GameActionReceipt:
            return self.receipts.get(cast(tuple[object, object], key))
        return None

    async def execute(self, statement: object, params: object = None) -> None:
        self.statements.append(statement)
        if (
            getattr(statement, "is_insert", False)
            and getattr(getattr(statement, "table", None), "name", "") == "jobs"
        ):
            compiled = statement.compile()  # type: ignore[union-attr]
            self.job_inserts.append(dict(compiled.params))

    def add(self, value: object) -> None:
        assert isinstance(value, GameActionReceipt)
        self.receipts[(value.operation_id, value.effect_number)] = value

    async def flush(self) -> None:
        return None


def session() -> FakeSession:
    return FakeSession()


async def test_view_shows_current_state_without_mutation() -> None:
    fake = session()
    before = (fake.ownership.last_game_action_at, fake.ownership.expires_at)
    state = await get_settings(cast(AsyncSession, fake), fake.actor)
    assert state.has_ownership and state.reminders_enabled
    assert state.ownership_id == fake.ownership.id and state.club_id == fake.club.id
    assert (fake.ownership.last_game_action_at, fake.ownership.expires_at) == before
    assert fake.receipts == {} and fake.job_inserts == []


async def test_explicit_disable_retry_never_toggles_back() -> None:
    fake = session()
    first = await set_reminders_enabled(
        cast(AsyncSession, fake), fake.actor, enabled=False, ownership_id=fake.ownership.id
    )
    assert (first.applied, first.duplicate) == (True, False)
    assert first.reminders_enabled is False and fake.manager.reminders_enabled is False
    second = await set_reminders_enabled(
        cast(AsyncSession, fake), fake.actor, enabled=False, ownership_id=fake.ownership.id
    )
    assert (second.applied, second.duplicate) == (False, True)
    assert fake.manager.reminders_enabled is False
    receipt = fake.receipts[(fake.actor.operation_id, 0)]
    assert receipt.kind == SETTINGS_KIND and receipt.ownership_id == fake.ownership.id


async def test_same_operation_cannot_set_opposite_preference() -> None:
    fake = session()
    await set_reminders_enabled(
        cast(AsyncSession, fake), fake.actor, enabled=False, ownership_id=fake.ownership.id
    )
    with pytest.raises(PermissionDenied, match="different value"):
        await set_reminders_enabled(
            cast(AsyncSession, fake), fake.actor, enabled=True, ownership_id=fake.ownership.id
        )
    assert fake.manager.reminders_enabled is False


async def test_ownership_expiring_while_waiting_for_locks_cannot_change_preferences() -> None:
    fake = session()
    with pytest.raises(PermissionDenied, match="waiting"):
        await set_reminders_enabled(
            cast(AsyncSession, fake),
            fake.actor,
            enabled=False,
            ownership_id=fake.ownership.id,
            clock=lambda: fake.ownership.expires_at,
        )
    assert fake.manager.reminders_enabled is True and fake.receipts == {}


async def test_stale_ownership_button_never_changes_new_state() -> None:
    fake = session()
    old = OwnershipPeriod(
        id=uuid4(),
        operation_key="op-old",
        manager_id=fake.manager.id,
        club_id=fake.club.id,
        claimed_at=NOW - timedelta(days=200),
        last_game_action_at=NOW - timedelta(days=200),
        expires_at=ownership_deadline(NOW - timedelta(days=200)),
        ended_at=NOW - timedelta(days=20),
        end_reason="inactive",
    )
    fake.old_ownership = old
    with pytest.raises(PermissionDenied):
        await set_reminders_enabled(
            cast(AsyncSession, fake), fake.actor, enabled=False, ownership_id=old.id
        )
    assert fake.manager.reminders_enabled is True
    assert fake.receipts == {}


async def test_expired_ownership_rejects_mutation() -> None:
    fake = session()
    fake.ownership.expires_at = NOW - timedelta(seconds=1)
    actor = Actor(fake.manager.id, 123, None, uuid4(), NOW)
    with pytest.raises(PermissionDenied):
        await set_reminders_enabled(
            cast(AsyncSession, fake), actor, enabled=False, ownership_id=fake.ownership.id
        )
    assert fake.manager.reminders_enabled is True


async def test_opt_out_without_analytics_still_applies_operational_dedupe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = session()
    # Opt-out/internal: no analytics identity, and any analytics write must fail.
    fake.actor = Actor(fake.manager.id, 123, None, uuid4(), NOW)

    async def forbidden(*args: object, **kwargs: object) -> bool:
        raise AssertionError("preferences must not emit journey events")

    import goaletto.infrastructure.analytics as analytics

    monkeypatch.setattr(analytics, "record_event", forbidden)
    outcome = await set_reminders_enabled(
        cast(AsyncSession, fake), fake.actor, enabled=False, ownership_id=fake.ownership.id
    )
    assert outcome.applied and fake.manager.reminders_enabled is False
    retry = await set_reminders_enabled(
        cast(AsyncSession, fake), fake.actor, enabled=False, ownership_id=fake.ownership.id
    )
    assert retry.duplicate and fake.manager.reminders_enabled is False


async def test_ownership_reminder_ignores_retired_preference() -> None:
    fake = session()
    await set_reminders_enabled(
        cast(AsyncSession, fake), fake.actor, enabled=False, ownership_id=fake.ownership.id
    )

    class SenderSession:
        def in_transaction(self) -> bool:
            return True

        async def scalar(self, statement: object) -> object | None:
            descriptions = getattr(statement, "column_descriptions", None)
            entity = descriptions[0].get("entity") if descriptions else None
            if entity is OwnershipPeriod:
                return fake.ownership
            return None

        async def get(self, model: object, key: object, **kwargs: object) -> object | None:
            if model is Manager:
                return fake.manager
            if model is Club:
                return fake.club
            return None

    message = OutboxMessage(
        id=uuid4(),
        dedupe_key="lease:v1:test",
        chat_id=123,
        payload={
            "version": 1,
            "kind": "ownership_reminder",
            "ownership_id": str(fake.ownership.id),
            "club_id": str(fake.club.id),
            "last_action_at": fake.ownership.last_game_action_at.isoformat(),
        },
        due_at=NOW,
        status="pending",
        attempts=0,
        priority=10,
    )
    decision = await check_recipient(cast(AsyncSession, SenderSession()), message, NOW)
    assert decision == Decision.DELIVER


async def test_reenable_restores_schedule_without_moving_deadline() -> None:
    fake = session()
    deadline = (fake.ownership.last_game_action_at, fake.ownership.expires_at)
    await set_reminders_enabled(
        cast(AsyncSession, fake), fake.actor, enabled=False, ownership_id=fake.ownership.id
    )
    fake.job_inserts.clear()
    await set_reminders_enabled(
        cast(AsyncSession, fake),
        Actor(fake.manager.id, 123, None, uuid4(), NOW),
        enabled=True,
        ownership_id=fake.ownership.id,
    )
    assert fake.manager.reminders_enabled is True
    assert (fake.ownership.last_game_action_at, fake.ownership.expires_at) == deadline
    kinds = [params.get("kind") for params in fake.job_inserts]
    assert kinds.count("ownership.remind") == 3
    assert kinds.count("ownership.expire") == 1
    # Expiry still targets the preserved six-month deadline.
    assert fake.ownership.expires_at == ownership_deadline(deadline[0])


async def test_preferences_never_extend_ownership_or_touch_availability() -> None:
    fake = session()
    fake.manager.bot_available = False
    before = (fake.ownership.last_game_action_at, fake.ownership.expires_at)
    outcome = await set_reminders_enabled(
        cast(AsyncSession, fake),
        Actor(fake.manager.id, 123, None, uuid4(), NOW),
        enabled=False,
        ownership_id=fake.ownership.id,
    )
    assert outcome.applied
    assert (fake.ownership.last_game_action_at, fake.ownership.expires_at) == before
    assert fake.manager.bot_available is False
    # Re-enable also leaves availability to the existing /start path.
    await set_reminders_enabled(
        cast(AsyncSession, fake),
        Actor(fake.manager.id, 123, None, uuid4(), NOW),
        enabled=True,
        ownership_id=fake.ownership.id,
    )
    assert fake.manager.bot_available is False
    assert (fake.ownership.last_game_action_at, fake.ownership.expires_at) == before


async def test_mutation_requires_caller_transaction() -> None:
    fake = session()
    fake.in_transaction = lambda: False  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="transaction"):
        await set_reminders_enabled(
            cast(AsyncSession, fake), fake.actor, enabled=False, ownership_id=fake.ownership.id
        )
    with pytest.raises(ValueError, match="transaction"):
        await get_settings(cast(AsyncSession, fake), fake.actor)


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
