from dataclasses import replace
from uuid import uuid4

import pytest

from goaletto.application.club_rename import action, screen, submit
from goaletto.application.game_actions import PermissionDenied
from goaletto.infrastructure.models import Club, ClubNameChoice, OwnershipPeriod, Season
from tests.integration.test_agent_wave import setup_world

pytestmark = pytest.mark.integration


def another(actor):
    return replace(actor, operation_id=uuid4())


async def test_preview_stale_confirm_once_and_history(sessions):
    _, actor, club_id = await setup_world(sessions)
    async with sessions.begin() as session:
        state = await screen(session, actor)
        owner = state.ownership_id
        before = state.name
        state = await action(session, another(actor), owner, "input")
        first = another(actor)
        state = await submit(session, first, " North   FC ")
        old = state.draft_id
        assert (await session.get(Club, club_id)).name == before
    async with sessions.begin() as session:
        state = await action(session, another(actor), owner, "input", old)
        state = await submit(session, another(actor), "South FC")
        latest = state.draft_id
        assert (await submit(session, first, " North   FC ")).draft_id == latest
    with pytest.raises(PermissionDenied):
        async with sessions.begin() as session:
            await action(session, another(actor), owner, "confirm", old)
    with pytest.raises(RuntimeError):
        async with sessions.begin() as session:
            await action(session, another(actor), owner, "confirm", latest)
            raise RuntimeError("rollback")
    async with sessions.begin() as session:
        assert (await session.get(Club, club_id)).name == before
        state = await action(session, another(actor), owner, "confirm", latest)
        assert state.name == "South FC" and state.state == "done"
        assert (await action(session, another(actor), owner, "confirm", latest)) == state
        assert (await action(session, another(actor), owner, "input", latest)) == state
        saved = await session.get(ClubNameChoice, owner)
        assert saved.original_name == before and saved.changed_at == actor.at


async def test_decline_waits_for_offseason_and_stale_ownership(sessions):
    result, actor, _ = await setup_world(sessions)
    async with sessions.begin() as session:
        state = await screen(session, actor)
        owner = state.ownership_id
        season = await session.get(Season, result.season_id)
        season.status = "active"
        await session.flush()
        state = await action(session, another(actor), owner, "keep")
        assert not state.allowed
        assert not (await action(session, another(actor), owner, "input")).allowed
        season.status = "scheduled"
        await session.flush()
        assert (await screen(session, actor)).allowed
        state = await action(session, another(actor), owner, "input")
        state = await submit(session, another(actor), "New FC")
        draft = state.draft_id
        current = await session.get(OwnershipPeriod, owner)
        current.ended_at, current.end_reason = actor.at, "voluntary"
    with pytest.raises(PermissionDenied):
        async with sessions.begin() as session:
            await action(session, another(actor), owner, "confirm", draft)
