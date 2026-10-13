"""Configured test identities stay excluded before attribution is recorded."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from goaletto.application.acquisition import accept_start
from goaletto.infrastructure.models import (
    AcquisitionTouch,
    AnalyticsManager,
    JourneyEventRow,
    Manager,
)

pytestmark = pytest.mark.integration


async def test_confirmed_internal_account_is_excluded_on_first_and_repeated_start(sessions):
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        first = await accept_start(
            session, telegram_id=1001, update_id=1, payload=None, at=now, internal=True
        )
        assert first.analytics_manager_id is None
    async with sessions.begin() as session:
        # A later default call must not turn a confirmed internal account public.
        again = await accept_start(session, telegram_id=1001, update_id=2, payload=None, at=now)
        assert again.manager_id == first.manager_id
        assert again.analytics_manager_id is None
        manager = await session.get(Manager, first.manager_id)
        assert manager.is_internal is True
        for model in (AcquisitionTouch, AnalyticsManager, JourneyEventRow):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
