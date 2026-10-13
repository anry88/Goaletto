from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select

from goaletto.application.game_actions import PermissionDenied
from goaletto.application.language import set_language
from goaletto.infrastructure.models import Manager, OwnershipPeriod
from tests.integration.test_agent_wave import setup_world

pytestmark = pytest.mark.integration


async def test_language_persists_and_old_retry_never_reverts_new_choice(sessions):
    _, actor, _ = await setup_world(sessions)
    first = replace(actor, operation_id=uuid4())
    async with sessions.begin() as session:
        owner = await session.scalar(
            select(OwnershipPeriod).where(OwnershipPeriod.manager_id == actor.manager_id)
        )
        expiry, last = owner.expires_at, owner.last_game_action_at
        assert await set_language(session, first, "en") == "en"
    async with sessions.begin() as session:
        manager = await session.get(Manager, actor.manager_id)
        assert manager.locale == "en"
        assert await set_language(session, replace(actor, operation_id=uuid4()), "ru") == "ru"
        assert await set_language(session, first, "en") == "ru"
        owner = await session.scalar(
            select(OwnershipPeriod).where(OwnershipPeriod.manager_id == actor.manager_id)
        )
        assert (owner.expires_at, owner.last_game_action_at) == (expiry, last)
        with pytest.raises(PermissionDenied, match="reused"):
            await set_language(session, first, "ru")
