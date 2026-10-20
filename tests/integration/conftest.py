"""Explicit development reset of the single Goaletto DB; no new databases."""

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from goaletto.config import Settings
from goaletto.infrastructure.models import Base


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    if os.environ.get("GOALETTO_ALLOW_DEVELOPMENT_RESET") != "1":
        pytest.skip("Set GOALETTO_ALLOW_DEVELOPMENT_RESET=1 for the player-free development DB")
    settings = Settings()
    if settings.environment != "development":
        raise pytest.UsageError("Destructive checks require development mode without real users")
    url = make_url(settings.database_url.get_secret_value())
    if url.drivername != "postgresql+asyncpg" or url.database != "goaletto":
        raise pytest.UsageError("Reset checks require the explicitly configured Goaletto database")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # Reset the one application DB only while development data has no real users.
    async with engine.connect() as connection:
        exists = await connection.scalar(text("SELECT to_regclass('public.data_protection')"))
        if exists and await connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM data_protection WHERE protected_at IS NOT NULL)")
        ):
            await engine.dispose()
            raise pytest.UsageError("Real Goaletto data: development reset is forbidden")
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    try:
        yield factory
    finally:
        await engine.dispose()
