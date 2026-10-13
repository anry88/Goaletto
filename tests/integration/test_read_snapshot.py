"""Read-only operator reports keep one snapshot while the worker changes data."""

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from goaletto.config import Settings
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.models import Job

pytestmark = pytest.mark.integration


async def test_operator_snapshot_is_repeatable_and_server_rejects_writes(sessions):
    database = Database(Settings())
    try:
        async with database.read_transaction() as reader:
            assert await reader.scalar(text("SHOW transaction_read_only")) == "on"
            assert await reader.scalar(text("SHOW transaction_isolation")) == "repeatable read"
            before = await reader.scalar(select(func.count()).select_from(Job))
            async with sessions.begin() as writer:
                await writer.execute(
                    text(
                        "INSERT INTO jobs (id,dedupe_key,kind,payload,due_at,status,attempts) "
                        "VALUES (gen_random_uuid(),'snapshot-check','ping','{}',now(),'pending',0)"
                    )
                )
            assert await reader.scalar(select(func.count()).select_from(Job)) == before
        async with database.read_transaction() as reader:
            assert await reader.scalar(select(func.count()).select_from(Job)) == before + 1
        with pytest.raises(DBAPIError):
            async with database.read_transaction() as reader:
                await reader.execute(text("DELETE FROM jobs WHERE false"))
    finally:
        await database.close()
