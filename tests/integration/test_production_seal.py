from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application.data_protection_cli import seal
from goaletto.infrastructure.models import DataProtection
from tests.integration.test_agent_wave import NOW

pytestmark = pytest.mark.integration


async def test_explicit_seal_is_permanent_before_first_owner_or_payment(sessions):
    # Roll back the complete experiment: this is the sole disposable LOCAL DB.
    with pytest.raises(RuntimeError, match="rollback experiment"):
        async with sessions.begin() as session:
            with pytest.raises(ValueError, match="production"):
                await seal(session, environment="development", at=NOW)
            assert await session.scalar(select(DataProtection.protected_at)) is None
            await seal(session, environment="production", at=NOW)
            await seal(session, environment="production", at=NOW + timedelta(days=1))
            assert await session.scalar(select(DataProtection.protected_at)) == NOW
            for query in (
                "TRUNCATE clubs CASCADE",
                "TRUNCATE data_protection",
                "UPDATE data_protection SET protected_at=NULL",
                "DELETE FROM data_protection",
            ):
                with pytest.raises(DBAPIError):
                    async with session.begin_nested():
                        await session.execute(text(query))
            raise RuntimeError("rollback experiment")
    async with sessions.begin() as session:
        assert await session.scalar(select(DataProtection.protected_at)) is None
