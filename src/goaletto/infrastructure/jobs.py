from datetime import UTC, datetime
from uuid import uuid5

from sqlalchemy import String, cast, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.application.game_actions import handle_lease_job
from goaletto.application.match_runtime import (
    RUNTIME_KINDS,
    NotReady,
    handle_calendar_job,
    job_refs,
)
from goaletto.application.training import apply_training
from goaletto.infrastructure.models import Fixture, Job, OperationAttempt


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


async def process_one_job(session: AsyncSession, *, at: datetime | None = None) -> bool:
    """Short database-only handler in the caller's transaction; no external delivery."""
    at = at or datetime.now(UTC)
    require_utc(at)
    paused = (
        select(Fixture.id)
        .where(
            cast(Fixture.id, String) == Job.payload["fixture_id"].astext, Fixture.status == "paused"
        )
        .exists()
    )
    statement = (
        select(Job)
        .where(Job.status == "pending", Job.due_at <= at, ~paused)
        .order_by(Job.due_at, Job.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    job = await session.scalar(statement)
    if job is None:
        return False
    job.attempts += 1
    outcome, failure = "committed", None
    fixture_id = None
    operation_id = uuid5(job.id, "operation:v1")
    if job.kind == "system.ping":
        job.status = "done"
        job.last_error = None
    elif job.kind in RUNTIME_KINDS | {"ownership.remind", "ownership.expire", "training.apply"}:
        try:
            async with session.begin_nested():
                if job.kind in RUNTIME_KINDS:
                    await handle_calendar_job(session, job, at)
                    _, fixture_id, _ = job_refs(job)
                elif job.kind == "training.apply":
                    await apply_training(session, job, at)
                else:
                    await handle_lease_job(session, job, at)
                if fixture_id is not None:
                    stage = {
                        "match.lock_lineup": "locked",
                        "match.start": "started",
                        "match.second_half_start": "second_half",
                        "match.remind": "reminder",
                        "match.finalize_result": "result",
                        "match.tick": f"tick:{job.payload['tick_number']}",
                    }[job.kind]
                    operation_id = uuid5(fixture_id, f"runtime:v1:{stage}")
            job.status = "done"
            job.last_error = None
        except NotReady:
            outcome, failure = "deferred", "not_ready"
            job.last_error = "not_ready"
        except (ValueError, TypeError, KeyError):
            outcome, failure = "failed", "invalid_state"
            job.status = "failed"
            job.last_error = "invalid_state"
    else:
        job.status = "failed"
        job.last_error = "unsupported_job_kind"
        outcome, failure = "failed", "unsupported_job_kind"
    session.add(
        OperationAttempt(
            id=uuid5(job.id, f"attempt:{job.attempts}"),
            job_id=job.id,
            operation_id=operation_id,
            fixture_id=fixture_id,
            retry_number=job.attempts,
            stage=job.kind,
            started_at=at,
            finished_at=max(at, datetime.now(UTC)),
            due_at=job.due_at,
            outcome=outcome,
            failure_class=failure,
        )
    )
    return outcome != "deferred"
