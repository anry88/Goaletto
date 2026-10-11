import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from goaletto.infrastructure.jobs import enqueue_ping, process_one_job
from goaletto.infrastructure.models import Job


@pytest_asyncio.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    url = os.environ.get("GOALETTO_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set GOALETTO_TEST_DATABASE_URL to an isolated migrated database")
    engine = create_async_engine(url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.integration
async def test_duplicate_enqueue_and_rollback_preserve_job(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    key = f"test-{uuid4()}"
    async with sessions.begin() as session:
        await enqueue_ping(session, key)
        await enqueue_ping(session, key)
    async with sessions() as session:
        assert await process_one_job(session)
        await session.rollback()
    async with sessions.begin() as session:
        job = await session.scalar(select(Job).where(Job.dedupe_key == key))
        assert job is not None and job.status == "pending"
        assert await process_one_job(session)
    async with sessions.begin() as session:
        job = await session.scalar(select(Job).where(Job.dedupe_key == key))
        assert job is not None and job.status == "done" and job.attempts == 1
        await session.delete(job)


@pytest.mark.integration
async def test_workers_skip_a_locked_due_job(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    keys = [f"test-{uuid4()}", f"test-{uuid4()}"]
    async with sessions.begin() as session:
        for key in keys:
            await enqueue_ping(session, key)
    async with sessions() as first, sessions() as second:
        locked = await first.scalar(select(Job).where(Job.dedupe_key == keys[0]).with_for_update())
        assert locked is not None
        assert await process_one_job(second)
        await second.commit()
        assert await process_one_job(first)
        await first.commit()
    async with sessions.begin() as session:
        jobs = list((await session.scalars(select(Job).where(Job.dedupe_key.in_(keys)))).all())
        assert len(jobs) == 2 and all(job.status == "done" for job in jobs)
        for job in jobs:
            await session.delete(job)
