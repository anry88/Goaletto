import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from goaletto.application.acquisition import TouchKind, accept_start, classify_start
from goaletto.infrastructure.acquisition_queries import source_funnel
from goaletto.infrastructure.analytics import start_coverage
from goaletto.infrastructure.models import (
    AcquisitionCampaign,
    AcquisitionFirstTouch,
    AcquisitionTouch,
)

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)


async def test_concurrent_unregistered_source_is_shared_and_first_touch_survives_return(sessions):
    async with sessions.begin() as session:
        await start_coverage(session, NOW)

    async def enter(uid):
        async with sessions.begin() as session:
            return await accept_start(
                session, telegram_id=uid, update_id=uid, payload="ai_match_prediction", at=NOW
            )

    first, second = await asyncio.gather(enter(1001), enter(1002))
    assert first.kind == second.kind == TouchKind.KNOWN
    assert first.campaign_id == second.campaign_id
    async with sessions.begin() as session:
        retry = await accept_start(
            session, telegram_id=1001, update_id=1001, payload="ignored_retry", at=NOW
        )
        assert not retry.created_touch and retry.campaign_id == first.campaign_id
        returned = await accept_start(
            session,
            telegram_id=1001,
            update_id=1003,
            payload="another_channel",
            at=NOW + timedelta(hours=1),
        )
        assert returned.campaign_id != first.campaign_id
        assert await session.scalar(select(func.count()).select_from(AcquisitionCampaign)) == 2
        assert await session.scalar(select(func.count()).select_from(AcquisitionTouch)) == 3
        assert (
            await session.get(AcquisitionFirstTouch, first.analytics_manager_id)
        ).campaign_id == first.campaign_id
        row = await session.get(AcquisitionCampaign, first.campaign_id)
        assert row.code == row.placement == "ai_match_prediction"
        report = await source_funnel(session, NOW, NOW + timedelta(days=1))
        assert len(report.rows) == 1 and report.rows[0]["starts"] == 2
        assert report.rows[0]["placement"] == "ai_match_prediction"


async def test_automatic_source_is_transactional_and_case_sensitive(sessions):
    with pytest.raises(RuntimeError, match="rollback"):
        async with sessions.begin() as session:
            await classify_start(session, "rolled_back", at=NOW)
            raise RuntimeError("rollback")
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(AcquisitionCampaign)) == 0
        lower = await classify_start(session, "ai_match_prediction", at=NOW)
        upper = await classify_start(session, "AI_match_prediction", at=NOW)
        assert lower[1] != upper[1]
        assert await classify_start(session, "A_" + "x" * 62, at=NOW) == (
            TouchKind.KNOWN,
            await session.scalar(
                select(AcquisitionCampaign.id).where(AcquisitionCampaign.code == "A_" + "x" * 62)
            ),
        )


async def test_internal_direct_and_invalid_starts_do_not_create_sources(sessions):
    async with sessions.begin() as session:
        await start_coverage(session, NOW)
        await accept_start(
            session,
            telegram_id=1001,
            update_id=1001,
            payload="internal_only",
            internal=True,
            at=NOW,
        )
        invalid = await accept_start(
            session, telegram_id=1002, update_id=1002, payload="bad/code", at=NOW
        )
        direct = await accept_start(session, telegram_id=1003, update_id=1003, payload=None, at=NOW)
        assert invalid.kind == TouchKind.UNKNOWN and direct.kind == TouchKind.DIRECT
        assert await session.scalar(select(func.count()).select_from(AcquisitionCampaign)) == 0
