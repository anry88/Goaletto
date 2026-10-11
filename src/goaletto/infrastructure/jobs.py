from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.infrastructure.models import Job


async def enqueue_ping(session: AsyncSession, dedupe_key: str) -> None:
    statement = (
        insert(Job)
        .values(
            dedupe_key=dedupe_key,
            kind="system.ping",
            payload={},
            due_at=datetime.now(UTC),
            status="pending",
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )
    await session.execute(statement)


async def process_one_job(session: AsyncSession) -> bool:
    """Short database-only handler in the caller's transaction; no external delivery."""
    statement = (
        select(Job)
        .where(Job.status == "pending", Job.due_at <= datetime.now(UTC))
        .order_by(Job.due_at, Job.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    job = await session.scalar(statement)
    if job is None:
        return False
    job.attempts += 1
    if job.kind == "system.ping":
        job.status = "done"
        job.last_error = None
    else:
        job.status = "failed"
        job.last_error = "unsupported_job_kind"
    return True
