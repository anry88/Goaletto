from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from goaletto.infrastructure.jobs import enqueue_ping, process_one_job
from goaletto.infrastructure.models import Job


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
