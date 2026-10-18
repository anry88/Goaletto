from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import String, cast, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.application.feedback import cleanup_comments
from goaletto.application.finances import cycle_finances
from goaletto.application.game_actions import allocation_lock, handle_lease_job
from goaletto.application.market import close_auction, prohibit_player_trades
from goaletto.application.match_runtime import (
    RUNTIME_KINDS,
    NotReady,
    handle_calendar_job,
    job_refs,
)
from goaletto.application.persons import AppearanceExhausted
from goaletto.application.scouting import complete_search
from goaletto.application.season_rollover import rollover_season
from goaletto.application.stadium import complete_expansion
from goaletto.application.training import apply_training
from goaletto.infrastructure.models import Fixture, Job, OperationAttempt, WorldAdmission


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


async def process_one_job(
    session: AsyncSession,
    *,
    at: datetime | None = None,
    replay_season_id: UUID | None = None,
) -> bool:
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
    from goaletto.application.world_capacity import replay_job_predicate

    preparing = select(WorldAdmission.season_id).where(WorldAdmission.ready_at.is_(None))
    if replay_season_id is None:
        admission_scope = or_(Job.kind == "world.catch_up", ~replay_job_predicate(preparing))
    else:
        if not await allocation_lock(session, try_lock=True):
            raise NotReady("Historical admission is busy")
        admission_scope = replay_job_predicate([replay_season_id]) & Job.kind.not_in(
            ("world.reserve", "world.catch_up", "season.rollover")
        )
        if (
            await session.scalar(
                select(WorldAdmission.season_id).where(
                    WorldAdmission.season_id == replay_season_id, WorldAdmission.ready_at.is_(None)
                )
            )
            is None
        ):
            raise ValueError("Historical execution requires an unopened admission")
    statement = (
        select(Job)
        .where(Job.status == "pending", Job.due_at <= at, ~paused, admission_scope)
        .order_by(Job.due_at, Job.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    job = await session.scalar(statement)
    if job is None:
        return False
    processed_at = at
    if replay_season_id is not None:
        at = job.due_at
    original_due = job.due_at
    rescheduled = False
    job.attempts += 1
    outcome, failure = "committed", None
    fixture_id = None
    operation_id = uuid5(job.id, "operation:v1")
    if job.kind == "system.ping":
        job.status = "done"
        job.last_error = None
    elif job.kind in RUNTIME_KINDS | {
        "ownership.remind",
        "ownership.expire",
        "training.apply",
        "finance.cycle",
        "calendar.materialize",
        "stadium.complete",
        "market.close",
        "market.cancel_player",
        "feedback.cleanup",
        "feedback.scan",
        "season.rollover",
        "scout.complete",
        "scout.expire",
        "staff.career_expire",
        "world.reserve",
        "world.catch_up",
        "runtime.reconcile",
    }:
        try:
            async with session.begin_nested():
                if job.kind == "feedback.scan":
                    from goaletto.application.feedback_automation import scan

                    await scan(session, job, at)
                    rescheduled = True
                elif job.kind == "runtime.reconcile":
                    from goaletto.application.runtime_reconciliation import reconcile

                    await reconcile(session, job, at)
                    rescheduled = True
                elif job.kind == "world.reserve":
                    from goaletto.application.world_capacity import reserve_step

                    await reserve_step(session, job, at)
                    rescheduled = True
                elif job.kind == "world.catch_up":
                    from goaletto.application.world_capacity import catch_up_step

                    rescheduled = await catch_up_step(session, job, at)
                elif job.kind in RUNTIME_KINDS:
                    rescheduled = await handle_calendar_job(session, job, at) == "rescheduled"
                    _, fixture_id, _ = job_refs(job)
                elif job.kind == "season.rollover":
                    season_id, _, _ = job_refs(job)
                    await rollover_season(session, season_id, at)
                elif job.kind == "feedback.cleanup":
                    if (
                        set(job.payload) != {"version", "retention_days"}
                        or type(job.payload["version"]) is not int
                        or job.payload["version"] != 1
                    ):
                        raise ValueError("Invalid feedback cleanup job")
                    await cleanup_comments(
                        session, at=at, retention_days=job.payload["retention_days"]
                    )
                    job.due_at = at + timedelta(days=1)
                    rescheduled = True
                elif job.kind == "market.close":
                    rescheduled = await close_auction(session, job, at)
                elif job.kind == "market.cancel_player":
                    from uuid import UUID

                    if (
                        set(job.payload) != {"version", "person_id"}
                        or type(job.payload["version"]) is not int
                        or job.payload["version"] != 1
                    ):
                        raise ValueError("Invalid retirement cancellation job")
                    if not await allocation_lock(session, try_lock=True):
                        raise NotReady("Busy retirement cancellation")
                    await prohibit_player_trades(session, UUID(job.payload["person_id"]), at)
                elif job.kind == "stadium.complete":
                    await complete_expansion(session, job, at)
                elif job.kind == "calendar.materialize":
                    from uuid import UUID

                    from goaletto.application.calendar_planning import plan_horizon

                    if set(job.payload) != {"version", "season_id"} or job.payload["version"] != 1:
                        raise ValueError("Invalid horizon job")
                    if not await allocation_lock(session, try_lock=True):
                        raise NotReady("Calendar allocation is busy")
                    await plan_horizon(session, UUID(job.payload["season_id"]), at)
                    from goaletto.infrastructure.models import Season

                    current = await session.get(Season, UUID(job.payload["season_id"]))
                    if current is not None and current.status in {"scheduled", "active"}:
                        job.due_at = at + timedelta(hours=1)
                        rescheduled = True
                elif job.kind in {"scout.complete", "scout.expire"}:
                    await complete_search(session, job, at)
                elif job.kind == "staff.career_expire":
                    from goaletto.application.staff_career import expire_job

                    await expire_job(session, job, at)
                elif job.kind == "finance.cycle":
                    await cycle_finances(session, job, at)
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
                        "match.extra_start": "extra_first",
                        "match.extra_second_half_start": "extra_second",
                        "match.penalty": f"penalty:{job.payload['tick_number']}",
                    }[job.kind]
                    operation_id = uuid5(fixture_id, f"runtime:v1:{stage}")
            job.status = "pending" if rescheduled else "done"
            outcome = "deferred" if rescheduled else "committed"
            job.last_error = None
        except (NotReady, AppearanceExhausted):
            outcome, failure = "deferred", "not_ready"
            job.last_error = "not_ready"
            if job.kind in {
                "season.rollover",
                "world.reserve",
                "world.catch_up",
                "runtime.reconcile",
                "feedback.scan",
            }:
                delay = 1 if job.kind == "world.catch_up" else 60
                job.due_at = at + timedelta(seconds=delay)
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
            stage=f"world.catch_up:{job.kind}" if replay_season_id else job.kind,
            started_at=processed_at,
            finished_at=max(processed_at, datetime.now(UTC)),
            due_at=original_due,
            outcome=outcome,
            failure_class=failure,
        )
    )
    return rescheduled or outcome != "deferred"
