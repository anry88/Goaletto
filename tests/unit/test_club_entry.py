"""Supplied ORM objects/SQL recording. No database or emulated PostgreSQL races."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from goaletto.application.analytics import Surface
from goaletto.application.game_actions import Actor, end_expired, handle_lease_job, user_effect
from goaletto.application.onboarding import (
    ClubUnavailable,
    claim_club,
    ensure_entry_capacity,
    next_initial_kickoff,
)
from goaletto.application.persons import AppearanceExhausted
from goaletto.domain.ownership import ownership_deadline
from goaletto.infrastructure.models import (
    AnalyticsManager,
    Club,
    GameActionReceipt,
    Job,
    Manager,
    OwnershipPeriod,
)

NOW = datetime(2026, 8, 31, 23, 42, tzinfo=UTC)


class Rows:
    def all(self):
        return []


class Recorder:
    def __init__(self):
        self.manager = Manager(
            id=uuid4(), telegram_id=123, reminders_enabled=True, bot_available=True
        )
        self.club = Club(
            id=uuid4(), league_id=uuid4(), name="Club", owner_telegram_id=None, budget=25000
        )
        self.actor = Actor(self.manager.id, 123, None, uuid4(), NOW)
        self.ownership = None
        self.receipts = {}
        self.statements = []
        self.added = []
        self.events = []
        self.nested = 0
        self.available = True

    def in_transaction(self):
        return True

    async def execute(self, statement, params=None):
        self.statements.append(statement)

    async def scalar(self, statement, params=None):
        self.statements.append(statement)
        if isinstance(statement, TextClause):
            return True
        entity = statement.column_descriptions[0].get("entity")
        if entity is Manager:
            return self.manager
        if entity is Club:
            return self.club if self.available else None
        if entity is AnalyticsManager:
            return None
        if entity is OwnershipPeriod:
            p = statement.compile().params
            if "operation_key_1" in p:
                return (
                    self.ownership
                    if self.ownership and self.ownership.operation_key == p["operation_key_1"]
                    else None
                )
            return self.ownership
        return 1

    async def scalars(self, statement):
        self.statements.append(statement)
        return Rows()

    async def get(self, model, key):
        if model is Manager:
            return self.manager
        return self.receipts.get(key)

    def add(self, value):
        self.added.append(value)
        if isinstance(value, OwnershipPeriod):
            self.ownership = value
        if isinstance(value, GameActionReceipt):
            self.receipts[(value.operation_id, value.effect_number)] = value

    async def flush(self):
        pass

    @asynccontextmanager
    async def begin_nested(self):
        self.nested += 1
        yield


@pytest.fixture(autouse=True)
def events(monkeypatch):
    async def record(session, event):
        session.events.append(event)
        return True

    monkeypatch.setattr("goaletto.application.game_actions.record_event", record)


async def claimed():
    recorder = Recorder()
    owner = await claim_club(cast(AsyncSession, recorder), recorder.actor, recorder.club.id)
    return recorder, owner


async def test_claim_keeps_budget_and_one_ownership_on_retry():
    recorder, owner = await claimed()
    assert recorder.club.budget == 25000 and recorder.club.owner_telegram_id == 123
    assert owner.expires_at == datetime(2027, 2, 28, 23, 42, tzinfo=UTC)
    count = len(recorder.added), len(recorder.statements)
    assert await claim_club(cast(AsyncSession, recorder), recorder.actor, recorder.club.id) is owner
    assert len(recorder.added) == count[0]
    inserts = [s for s in recorder.statements if getattr(s, "is_insert", False)]
    assert len(inserts) == 4 and all(s.table.name == "jobs" for s in inserts)
    assert all(
        "attempts =" not in str(s.compile(dialect=postgresql.dialect())).split("DO UPDATE SET")[1]
        for s in inserts
    )
    club_sql = next(
        s
        for s in recorder.statements
        if getattr(s, "is_select", False) and s.column_descriptions[0].get("entity") is Club
    )
    assert club_sql.compile().params["tier_1"] == 5
    assert "FOR UPDATE OF clubs" in str(club_sql.compile(dialect=postgresql.dialect()))


async def test_occupied_or_upper_tier_query_result_never_creates_an_ownership():
    recorder = Recorder()
    recorder.available = False
    with pytest.raises(ClubUnavailable):
        await claim_club(cast(AsyncSession, recorder), recorder.actor, recorder.club.id)
    assert (
        recorder.added == []
        and recorder.club.budget == 25000
        and recorder.club.owner_telegram_id is None
    )


async def test_operational_dedupe_works_without_analytics_and_does_not_repeat_lease_extension():
    recorder, owner = await claimed()
    actor = replace(recorder.actor, operation_id=uuid4(), at=NOW + timedelta(days=10))
    assert await user_effect(
        cast(AsyncSession, recorder),
        actor,
        "club_opened",
        Surface.CLUB,
        ownership=owner,
        club_id=recorder.club.id,
    )
    deadline = owner.expires_at
    assert not await user_effect(
        cast(AsyncSession, recorder),
        replace(actor, at=actor.at + timedelta(days=20)),
        "club_opened",
        Surface.CLUB,
        ownership=owner,
        club_id=recorder.club.id,
    )
    assert owner.expires_at == deadline and deadline == ownership_deadline(actor.at)
    assert recorder.events == []


async def test_disabled_analytics_cannot_bypass_required_game_references():
    recorder, owner = await claimed()
    at = replace(recorder.actor, operation_id=uuid4(), at=NOW + timedelta(days=10))
    deadline = owner.expires_at
    with pytest.raises(ValueError, match="durable references"):
        await user_effect(
            cast(AsyncSession, recorder),
            at,
            "lineup_saved",
            Surface.SQUAD,
            ownership=owner,
            club_id=recorder.club.id,
        )
    with pytest.raises(ValueError, match="effect envelope"):
        await user_effect(
            cast(AsyncSession, recorder),
            at,
            "club_opened",
            Surface.CLUB,
            effect=True,
            ownership=owner,
            club_id=recorder.club.id,
        )
    assert owner.expires_at == deadline


async def test_release_is_idempotent_keeps_club_state_and_return_before_deadline_prevents_it():
    recorder, owner = await claimed()
    assert not await end_expired(
        cast(AsyncSession, recorder), owner, owner.expires_at - timedelta(seconds=1)
    )
    expiry = owner.expires_at
    assert await end_expired(cast(AsyncSession, recorder), owner, expiry + timedelta(days=1))
    assert owner.ended_at == expiry and owner.end_reason == "inactive"
    assert recorder.club.owner_telegram_id is None and recorder.club.budget == 25000
    assert not await end_expired(cast(AsyncSession, recorder), owner, expiry + timedelta(days=2))
    assert len(recorder.events) == 1 and recorder.events[0].origin.value == "system"


async def test_stale_or_opted_out_reminder_never_creates_outbox():
    recorder, owner = await claimed()
    work = Job(
        id=uuid4(),
        kind="ownership.remind",
        payload={
            "version": 1,
            "ownership_id": str(owner.id),
            "last_action_at": owner.last_game_action_at.isoformat(),
            "month": 1,
        },
    )
    recorder.statements = []
    recorder.manager.reminders_enabled = False
    await handle_lease_job(cast(AsyncSession, recorder), work, NOW + timedelta(days=40))
    assert not any(getattr(s, "is_insert", False) for s in recorder.statements)

    recorder.manager.reminders_enabled = True
    owner.last_game_action_at = NOW + timedelta(days=1)
    await handle_lease_job(cast(AsyncSession, recorder), work, NOW + timedelta(days=40))
    assert not any(getattr(s, "is_insert", False) for s in recorder.statements)


async def test_opted_out_manager_schedules_expiry_without_new_reminders():
    recorder = Recorder()
    recorder.manager.reminders_enabled = False
    await claim_club(cast(AsyncSession, recorder), recorder.actor, recorder.club.id)
    inserts = [s for s in recorder.statements if getattr(s, "is_insert", False)]
    assert len(inserts) == 1 and inserts[0].compile().params["kind"] == "ownership.expire"


async def test_exhausted_faces_leave_expansion_unavailable_without_activation(monkeypatch):
    recorder = Recorder()

    async def exhausted(*args, **kwargs):
        raise AppearanceExhausted("No unused portraits")

    async def forbidden(*args, **kwargs):
        raise AssertionError("An incomplete world must never activate")

    monkeypatch.setattr("goaletto.application.onboarding.initialize_world", exhausted)
    monkeypatch.setattr("goaletto.application.onboarding.activate_season", forbidden)
    assert not await ensure_entry_capacity(cast(AsyncSession, recorder), NOW)
    assert recorder.nested == 1 and recorder.added == []


def test_expansion_preparation_window_and_invalid_actor_identity():
    early = datetime(2026, 10, 17, 17, 54, 58, tzinfo=UTC)
    cutoff = early + timedelta(seconds=1)
    assert next_initial_kickoff(early) == datetime(2026, 10, 17, 18, tzinfo=UTC)
    assert next_initial_kickoff(cutoff) == datetime(2026, 10, 24, 18, tzinfo=UTC)
    with pytest.raises(ValueError):
        Actor(uuid4(), True, None, uuid4(), NOW)
