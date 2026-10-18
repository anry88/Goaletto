"""Launch gate: real cleanup/export contracts; no new database or roles."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from goaletto.application.analytics_cleanup_cli import apply
from goaletto.application.onboarding import begin_game
from goaletto.infrastructure.models import (
    AcquisitionFirstTouch,
    GameActionReceipt,
    JourneyEventRow,
    JourneySession,
    Manager,
    OwnershipPeriod,
)
from tests.integration.test_agent_wave import NOW, setup_world

pytestmark = pytest.mark.integration


async def test_cleanup_boundary_is_idempotent_and_does_not_recreate_launch_cohort(sessions):
    _, actor, _ = await setup_world(sessions)
    async with sessions.begin() as session:
        owner = await session.scalar(select(OwnershipPeriod))
        snapshot = (owner.id, owner.last_game_action_at, owner.expires_at)
        receipts = await session.scalar(select(func.count()).select_from(GameActionReceipt))
        events = await session.scalar(select(func.count()).select_from(JourneyEventRow))
        assert events > 0
    # Exactly at retention boundary is retained, strictly older is deleted.
    await apply(NOW + timedelta(days=180))
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(JourneyEventRow)) == events
    await apply(NOW + timedelta(days=180, microseconds=1))
    await apply(NOW + timedelta(days=180, microseconds=1))
    async with sessions.begin() as session:
        for model in (JourneyEventRow, JourneySession, AcquisitionFirstTouch):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
        owner = await session.scalar(select(OwnershipPeriod))
        assert (owner.id, owner.last_game_action_at, owner.expires_at) == snapshot
        assert await session.scalar(select(func.count()).select_from(GameActionReceipt)) == receipts
        manager = await session.get(Manager, actor.manager_id)
        assert manager.first_game_start_at == NOW
        await begin_game(
            session,
            telegram_id=1001,
            update_id=99001,
            payload=None,
            at=NOW + timedelta(days=181),
        )
        assert await session.scalar(select(func.count()).select_from(AcquisitionFirstTouch)) == 0


async def test_export_is_bounded_external_only_and_not_granted_to_public(sessions):
    _, actor, _ = await setup_world(sessions)
    async with sessions.begin() as session:
        await begin_game(
            session, telegram_id=9001, update_id=99002, payload=None, at=NOW, internal=True
        )
        rows = (await session.execute(text("SELECT * FROM journey_export"))).mappings().all()
        assert rows and {r["analytics_manager_id"] for r in rows} == {actor.analytics_id}
        assert set(rows[0]) == {
            "id",
            "version",
            "name",
            "origin",
            "analytics_manager_id",
            "session_id",
            "occurred_at",
            "recorded_at",
            "user_activity",
        }
        assert await session.scalar(
            text("""
            SELECT 'security_barrier=true'=ANY(reloptions)
            FROM pg_class WHERE oid='public.journey_export'::regclass
        """)
        )
        assert (
            await session.scalar(
                text("""
            SELECT count(*) FROM pg_class c
            CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a
            WHERE c.relnamespace='public'::regnamespace AND a.grantee=0
              AND c.relname IN ('journey_export','journey_events','analytics_managers',
                               'feedback_comments','feedback_responses')
        """)
            )
            == 0
        )
