"""Proactive tier-five stock and durable, owner-free historical bot replay."""

from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Select, String, cast, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from goaletto.application.analytics import require_utc
from goaletto.application.game_actions import allocation_lock
from goaletto.application.match_runtime import NotReady, activate_season
from goaletto.application.world import initialize_world
from goaletto.domain.finances import INITIAL_CAPITAL
from goaletto.domain.game_cycle import midnight
from goaletto.domain.world import WorldOptions, stable_seed
from goaletto.infrastructure.analytics import start_coverage
from goaletto.infrastructure.models import (
    Club,
    FixturePlan,
    Job,
    League,
    LeagueSet,
    OwnershipPeriod,
    Season,
    WorldAdmission,
)

LOW_WATER = 10
RESERVE_TARGET = 20
REPLAY_BATCH = 40
RESERVE_JOB = uuid5(NAMESPACE_URL, "goaletto:world-reserve:v1")


def ready_set_predicate() -> ColumnElement[bool]:
    return (
        ~select(WorldAdmission.season_id)
        .where(
            WorldAdmission.league_set_id == League.league_set_id,
            WorldAdmission.ready_at.is_(None),
        )
        .exists()
    )


async def available_clubs(session: AsyncSession) -> int:
    active = (
        select(OwnershipPeriod.id)
        .where(OwnershipPeriod.club_id == Club.id, OwnershipPeriod.ended_at.is_(None))
        .exists()
    )
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Club)
            .join(League, League.id == Club.league_id)
            .where(
                League.tier == 5, Club.owner_telegram_id.is_(None), ~active, ready_set_predicate()
            )
        )
        or 0
    )


async def request_reserve(session: AsyncSession, at: datetime) -> None:
    require_utc(at)
    # Do not revive a failed operator-visible job automatically.
    await session.execute(
        insert(Job)
        .values(
            id=RESERVE_JOB,
            dedupe_key="world-reserve:v1",
            kind="world.reserve",
            payload={"version": 1},
            due_at=at,
            status="pending",
            attempts=0,
        )
        .on_conflict_do_update(
            index_elements=["dedupe_key"],
            set_={
                "due_at": func.least(Job.due_at, at),
            },
            where=Job.status == "pending",
        )
    )


async def reserve_step(session: AsyncSession, job: Job, at: datetime) -> None:
    if job.payload != {"version": 1} or job.kind != "world.reserve":
        raise ValueError("Unsupported reserve job")
    if not await allocation_lock(session, try_lock=True):
        raise NotReady("World allocation is busy")
    if await available_clubs(session) > LOW_WATER:
        job.due_at = at + timedelta(minutes=1)
        return
    pending = await session.scalar(
        select(WorldAdmission.season_id).where(WorldAdmission.ready_at.is_(None)).limit(1)
    )
    if pending is not None:
        job.due_at = at + timedelta(minutes=1)
        return
    # All current sets created by admission share one calendar. Never silently
    # select an arbitrary old divergent operator/dev world as the reference.
    latest = (
        select(Season.league_set_id, func.max(Season.number).label("number"))
        .group_by(Season.league_set_id)
        .subquery()
    )
    anchors = (
        await session.scalars(
            select(Season)
            .join(
                latest,
                (Season.league_set_id == latest.c.league_set_id)
                & (Season.number == latest.c.number),
            )
            .order_by(Season.id)
        )
    ).all()
    if len({(s.cycle_epoch, s.ends_at) for s in anchors}) > 1:
        raise NotReady("Existing sets require calendar reconciliation")
    anchor = anchors[0] if anchors else None
    if anchor is not None:
        if anchor.cycle_epoch is None:
            raise ValueError("Reference calendar is missing")
        kickoff = anchor.starts_at
        replay_from = min(at, midnight(anchor.cycle_epoch))
    else:
        from goaletto.application.onboarding import next_initial_kickoff

        kickoff, replay_from = next_initial_kickoff(at), at
    count = int(await session.scalar(select(func.count()).select_from(LeagueSet)) or 0)
    key = "initial" if count == 0 else f"auto-{count + 1:04d}"
    result = await initialize_world(
        session,
        WorldOptions(
            key=key,
            seed=0 if count == 0 else stable_seed(0, key),
            first_kickoff=kickoff,
            initial_budget=INITIAL_CAPITAL,
        ),
        initialized_at=at,
        replay_from=replay_from,
        season_number=anchor.number if anchor is not None else 1,
    )
    session.add(
        WorldAdmission(
            season_id=result.season_id,
            league_set_id=result.league_set_id,
            reference_season_id=anchor.id if anchor else None,
            replay_from=replay_from,
            requested_at=at,
            ready_at=None,
        )
    )
    await session.flush()
    # Activation uses a virtual calendar clock. Coverage starts when the worker
    # really observes this world, never at the historical simulation boundary.
    await start_coverage(session, at)
    await activate_season(session, result.season_id, replay_from)
    await session.execute(
        insert(Job)
        .values(
            id=uuid5(result.season_id, "world-catch-up:v1"),
            dedupe_key=f"world-catch-up:v1:{result.season_id}",
            kind="world.catch_up",
            payload={"version": 1, "season_id": str(result.season_id)},
            due_at=at,
            status="pending",
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )
    job.due_at = at + timedelta(minutes=1)


def replay_job_predicate(season_ids: list[UUID] | Select[tuple[UUID]]) -> ColumnElement[bool]:
    clubs = (
        select(cast(Club.id, String))
        .join(League, League.id == Club.league_id)
        .join(WorldAdmission, WorldAdmission.league_set_id == League.league_set_id)
        .where(WorldAdmission.season_id.in_(season_ids))
    )
    return or_(
        Job.payload["season_id"].astext.in_(
            select(cast(WorldAdmission.season_id, String)).where(
                WorldAdmission.season_id.in_(season_ids)
            )
        ),
        Job.payload["club_id"].astext.in_(clubs),
    )


async def catch_up_step(session: AsyncSession, job: Job, at: datetime) -> bool:
    if (
        set(job.payload) != {"version", "season_id"}
        or job.payload["version"] != 1
        or job.kind != "world.catch_up"
    ):
        raise ValueError("Unsupported historical bot job")
    if not await allocation_lock(session, try_lock=True):
        raise NotReady("World allocation is busy")
    season_id = UUID(job.payload["season_id"])
    admission = await session.get(
        WorldAdmission, season_id, with_for_update=True, populate_existing=True
    )
    if admission is None:
        raise ValueError("Replay admission is missing")
    if admission.ready_at is not None:
        return False
    owned = await session.scalar(
        select(Club.id)
        .join(League, League.id == Club.league_id)
        .where(League.league_set_id == admission.league_set_id, Club.owner_telegram_id.is_not(None))
        .limit(1)
    )
    if owned is not None:
        raise ValueError("Historical replay cannot touch an owned club")
    from goaletto.infrastructure.jobs import process_one_job

    # Transient observation metadata only; every checkpoint/job/result commits
    # durably. Replayed system events record their real processing time.
    previous = session.info.get("world_replay_recorded_at")
    session.info["world_replay_recorded_at"] = at
    progress = 0
    try:
        for _ in range(REPLAY_BATCH):
            if not await process_one_job(session, at=at, replay_season_id=season_id):
                break
            progress += 1
    finally:
        if previous is None:
            session.info.pop("world_replay_recorded_at", None)
        else:
            session.info["world_replay_recorded_at"] = previous
    scope = replay_job_predicate([season_id])
    failed = await session.scalar(select(Job.id).where(scope, Job.status == "failed").limit(1))
    if failed is not None:
        raise ValueError("Historical bot replay requires repair")
    pending = await session.scalar(
        select(Job.id)
        .where(
            scope,
            Job.kind.not_in(("world.catch_up", "season.rollover")),
            Job.status == "pending",
            Job.due_at <= at,
        )
        .limit(1)
    )
    if pending is not None:
        job.due_at = at if progress else at + timedelta(seconds=1)
        # Commit a bounded batch, then let other worker jobs run.
        return True
    missed = await session.scalar(
        select(FixturePlan.id)
        .where(
            FixturePlan.season_id == season_id,
            FixturePlan.assigned_day < at.date(),
            FixturePlan.announced_at.is_(None),
            FixturePlan.home_club_id.is_not(None),
            FixturePlan.away_club_id.is_not(None),
        )
        .limit(1)
    )
    if missed is not None:
        raise ValueError("Historical calendar has an unprocessed match day")
    admission.ready_at = at
    await session.flush()
    await request_reserve(session, at)
    return False
