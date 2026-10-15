"""Durable preference/decision receipts on the single Goaletto development DB."""

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from goaletto.application.game_actions import PermissionDenied
from goaletto.application.kickoff_preferences import (
    fixture_kickoff_decision,
    set_kickoff_preference,
)
from goaletto.application.notification_preferences import get_settings
from goaletto.infrastructure.models import (
    Fixture,
    KickoffDecisionReceipt,
    KickoffPreferenceReceipt,
    Manager,
    OwnershipPeriod,
)
from tests.integration.test_agent_wave import NOW, setup_world

pytestmark = pytest.mark.integration


async def test_preferences_retry_restart_rollback_and_fixture_immutability(sessions):
    _, actor, club_id = await setup_world(sessions)
    first = replace(actor, operation_id=uuid4())
    async with sessions.begin() as session:
        owner = await session.scalar(
            select(OwnershipPeriod).where(OwnershipPeriod.manager_id == actor.manager_id)
        )
        before = owner.expires_at, owner.last_game_action_at
        fixtures = list((await session.scalars(select(Fixture).order_by(Fixture.id))).all())
        starts = [(f.id, f.scheduled_start_at) for f in fixtures]
        await set_kickoff_preference(session, first, zone="Asia/Kathmandu", minute=1140)
    async with sessions.begin() as session:
        state = await get_settings(session, actor)
        assert state.timezone_name == "Asia/Kathmandu" and state.preferred_kickoff_minute == 1140
        assert (state.expires_at, state.last_game_action_at) == before
        later = replace(actor, operation_id=uuid4())
        await set_kickoff_preference(session, later, zone="Europe/Belgrade", minute=1200)
        assert (
            await set_kickoff_preference(session, first, zone="Asia/Kathmandu", minute=1140)
        ).timezone_name == "Europe/Belgrade"
        assert await session.scalar(select(func.count()).select_from(KickoffPreferenceReceipt)) == 2
        assert starts == list(
            (
                await session.execute(
                    select(Fixture.id, Fixture.scheduled_start_at).order_by(Fixture.id)
                )
            ).all()
        )
    with pytest.raises(PermissionDenied, match="reused"):
        async with sessions.begin() as session:
            await set_kickoff_preference(session, first, zone="UTC")
    with pytest.raises(RuntimeError):
        async with sessions.begin() as session:
            await set_kickoff_preference(session, replace(actor, operation_id=uuid4()), zone="UTC")
            raise RuntimeError("rollback")
    async with sessions.begin() as session:
        assert (await session.get(Manager, actor.manager_id)).timezone_name == "Europe/Belgrade"
        other = next(
            f.away_club_id if f.home_club_id == club_id else f.home_club_id
            for f in fixtures
            if club_id in (f.home_club_id, f.away_club_id)
        )
    key = uuid4()
    args = dict(
        fixture_key=key,
        day=date(2026, 10, 17),
        home_club_id=club_id,
        away_club_id=other,
        at=NOW,
        seed=12,
    )

    async def decide():
        async with sessions.begin() as session:
            return await fixture_kickoff_decision(session, **args)

    decisions = await asyncio.wait_for(asyncio.gather(decide(), decide()), 20)
    assert decisions[0] == decisions[1]
    assert decisions[0].kickoff == datetime(2026, 10, 17, 18, tzinfo=UTC)
    async with sessions.begin() as session:
        await set_kickoff_preference(session, replace(actor, operation_id=uuid4()), minute=0)
        assert await fixture_kickoff_decision(session, **args) == decisions[0]
        assert await session.scalar(select(func.count()).select_from(KickoffDecisionReceipt)) == 1
        with pytest.raises(ValueError, match="reused"):
            await fixture_kickoff_decision(session, **{**args, "seed": 13})


async def test_antipodal_decision_persists_tie_across_preference_changes(sessions):
    from goaletto.application.onboarding import begin_game, claim_club

    _, first, home = await setup_world(sessions)
    async with sessions.begin() as session:
        entry = await begin_game(session, telegram_id=1002, update_id=103, payload=None, at=NOW)
        second = replace(entry.actor, operation_id=uuid4(), update_id=2)
        owner = await claim_club(session, second, entry.offers[0].id)
        await set_kickoff_preference(
            session, replace(first, operation_id=uuid4()), zone="UTC", minute=0
        )
        await set_kickoff_preference(
            session, replace(second, operation_id=uuid4()), zone="UTC", minute=720
        )
        away = owner.club_id
    args = dict(day=date(2026, 10, 17), home_club_id=home, away_club_id=away, at=NOW, seed=1)
    key = uuid4()
    async with sessions.begin() as session:
        result = await fixture_kickoff_decision(session, fixture_key=key, **args)
        assert result.tie_break == 1 and result.kickoff.hour == 18
        reversed_result = await fixture_kickoff_decision(
            session, fixture_key=uuid4(), **{**args, "home_club_id": away, "away_club_id": home}
        )
        assert reversed_result == result
    async with sessions.begin() as session:
        await set_kickoff_preference(session, replace(first, operation_id=uuid4()), minute=600)
        assert await fixture_kickoff_decision(session, fixture_key=key, **args) == result
        receipt = await session.get(KickoffDecisionReceipt, key)
        assert receipt.tie_break == 1 and receipt.seed == 1
