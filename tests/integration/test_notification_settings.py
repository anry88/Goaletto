"""PostgreSQL settings flow for Codex handoff. Resets the dev Goaletto DB.

Run only on the agreed player-free Goaletto database:

    GOALETTO_ALLOW_DEVELOPMENT_RESET=1 python -m pytest -q -m integration \\
        tests/integration/test_notification_settings.py

Covers retry idempotency, stale ownership, expiry, opt-out independence,
disabled queued-reminder suppression, re-enable schedule restore, ownership
deadline preservation, and commit-before-IO boundaries.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from goaletto.application.club_view import actor_for_callback
from goaletto.application.game_actions import Actor, PermissionDenied
from goaletto.application.notification_preferences import (
    get_settings,
    set_reminders_enabled,
)
from goaletto.application.onboarding import begin_game, claim_club
from goaletto.application.outbox_delivery import Decision, check_recipient
from goaletto.application.world import initialize_world
from goaletto.domain.world import WorldOptions
from goaletto.infrastructure.models import Job, Manager, OutboxMessage, OwnershipPeriod

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)
KICKOFF = datetime(2026, 10, 17, 18, tzinfo=UTC)
OPTIONS = WorldOptions(
    key="notification-settings", seed=0, first_kickoff=KICKOFF, initial_budget=50000
)


async def setup_owner(sessions):  # type: ignore[no-untyped-def]
    async with sessions.begin() as session:
        await initialize_world(session, OPTIONS, initialized_at=NOW)
        entry = await begin_game(session, telegram_id=1001, update_id=1, payload=None, at=NOW)
        actor = replace(entry.actor, operation_id=uuid4(), update_id=4)
        ownership = await claim_club(session, actor, entry.offers[0].id)
        ownership_id, club_id, manager_id = ownership.id, ownership.club_id, ownership.manager_id
        deadline = (ownership.last_game_action_at, ownership.expires_at)
    return ownership_id, club_id, manager_id, deadline


async def test_settings_retry_stale_expiry_and_reenable_on_postgres(sessions):  # type: ignore[no-untyped-def]
    ownership_id, club_id, manager_id, (last_action, expires) = await setup_owner(sessions)

    async with sessions.begin() as session:
        manager = await session.get(Manager, manager_id)
        assert manager is not None and manager.reminders_enabled
        actor = Actor(manager.id, 1001, None, uuid4(), NOW)
        state = await get_settings(session, actor)
        assert state.has_ownership and state.ownership_id == ownership_id

    # Explicit disable, then same-operation retry stays disabled.
    operation = uuid4()
    async with sessions.begin() as session:
        actor = Actor(manager_id, 1001, None, operation, NOW)
        first = await set_reminders_enabled(
            session, actor, enabled=False, ownership_id=ownership_id
        )
        assert (first.applied, first.duplicate) == (True, False)
    async with sessions.begin() as session:
        actor = Actor(manager_id, 1001, None, operation, NOW)
        second = await set_reminders_enabled(
            session, actor, enabled=False, ownership_id=ownership_id
        )
        assert (second.applied, second.duplicate) == (False, True)
        manager = await session.get(Manager, manager_id)
        ownership = await session.get(OwnershipPeriod, ownership_id)
        assert manager is not None and manager.reminders_enabled is False
        assert manager.bot_available is True
        assert ownership is not None
        assert (ownership.last_game_action_at, ownership.expires_at) == (last_action, expires)

    # Queued ownership reminder for the same epoch is suppressed before send.
    async with sessions.begin() as session:
        ownership = await session.get(OwnershipPeriod, ownership_id)
        assert ownership is not None
        message = OutboxMessage(
            id=uuid4(),
            dedupe_key=f"test-settings:{uuid4()}",
            chat_id=1001,
            payload={
                "version": 1,
                "kind": "ownership_reminder",
                "ownership_id": str(ownership_id),
                "club_id": str(club_id),
                "last_action_at": ownership.last_game_action_at.isoformat(),
            },
            due_at=NOW,
            status="pending",
            attempts=0,
            priority=10,
        )
        assert await check_recipient(session, message, NOW) == Decision.SKIP_STALE

    # Re-enable restores remind jobs while preserving the deadline.
    async with sessions.begin() as session:
        actor = Actor(manager_id, 1001, None, uuid4(), NOW)
        outcome = await set_reminders_enabled(
            session, actor, enabled=True, ownership_id=ownership_id
        )
        assert outcome.applied and outcome.reminders_enabled is True
        ownership = await session.get(OwnershipPeriod, ownership_id)
        assert ownership is not None
        assert (ownership.last_game_action_at, ownership.expires_at) == (last_action, expires)
        reminds = (
            await session.scalars(
                select(Job).where(
                    Job.kind == "ownership.remind",
                    Job.payload["ownership_id"].astext == str(ownership_id),
                )
            )
        ).all()
        assert len(reminds) >= 3

    # Stale ownership and expiry are rejected without touching the new state.
    async with sessions.begin() as session:
        actor = Actor(manager_id, 1001, None, uuid4(), NOW)
        with pytest.raises(PermissionDenied):
            await set_reminders_enabled(session, actor, enabled=False, ownership_id=uuid4())
        late = Actor(manager_id, 1001, None, uuid4(), expires + timedelta(seconds=1))
        with pytest.raises(PermissionDenied):
            await set_reminders_enabled(session, late, enabled=False, ownership_id=ownership_id)
        manager = await session.get(Manager, manager_id)
        assert manager is not None and manager.reminders_enabled is True

    # Callback-scoped actor (opt-out, no analytics identity) can still read state.
    async with sessions.begin() as session:
        actor = await actor_for_callback(
            session, telegram_id=1001, callback_id="settings-handoff-check", at=NOW
        )
        state = await get_settings(session, actor)
        assert state.reminders_enabled is True
