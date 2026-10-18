"""Restore only a missing immediate live continuation from a verified checkpoint.

No simulation here, no replacement of failed/done jobs and no result/ledger repair.
The normal worker grants a new decision window if the restored deadline is late.
"""

from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.game_actions import allocation_lock
from goaletto.application.match_runtime import NotReady, next_due
from goaletto.domain.calendar import CALENDAR_VERSION
from goaletto.domain.clock import MatchPhase
from goaletto.domain.match_codec import restore_state, state_hash
from goaletto.infrastructure.models import (
    Fixture,
    Job,
    Match,
    MatchStage,
    MatchTick,
    Season,
    WorldAdmission,
)

JOB_ID = uuid5(NAMESPACE_URL, "goaletto:runtime-reconcile:v1")
BATCH = 50


async def request_reconciliation(session: AsyncSession, at: datetime) -> None:
    await session.execute(
        insert(Job)
        .values(
            id=JOB_ID,
            dedupe_key="runtime-reconcile:v1",
            kind="runtime.reconcile",
            payload={"version": 1},
            due_at=at,
            status="pending",
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )


async def reconcile(session: AsyncSession, job: Job, at: datetime) -> None:
    if job.payload.get("version") != 1:
        raise ValueError("Unsupported reconciliation")
    if not await allocation_lock(session, try_lock=True):
        raise NotReady("Allocation is busy")
    after = UUID(job.payload["after"]) if job.payload.get("after") else None
    preparing = select(WorldAdmission.season_id).where(WorldAdmission.ready_at.is_(None))
    query = (
        select(Fixture)
        .join(Match, Match.fixture_id == Fixture.id)
        .join(Season, Season.id == Fixture.season_id)
        .where(
            Fixture.status == "live",
            Season.calendar_enabled.is_(True),
            ~Fixture.season_id.in_(preparing),
        )
    )
    if after is not None:
        query = query.where(Fixture.id > after)
    fixtures = (
        await session.scalars(query.order_by(Fixture.id).limit(BATCH).with_for_update(of=Fixture))
    ).all()
    restored = corrupt = conflicts = 0
    for fixture in fixtures:
        match = await session.scalar(
            select(Match).where(Match.fixture_id == fixture.id).with_for_update()
        )
        assert match is not None
        try:
            state = restore_state(match.state)
            digest = state_hash(state)
            if (
                state.tick != match.completed_tick
                or state.phase.value != match.phase
                or state.engine_version != match.engine_version
                or state.home.stats.goals != match.home_goals
                or state.away.stats.goals != match.away_goals
                or match.next_due_at is None
                or next_due(state, match, fixture) != match.next_due_at
            ):
                raise ValueError("Checkpoint projection mismatch")
            proven = await session.scalar(
                select(MatchTick.match_id).where(
                    MatchTick.match_id == match.id,
                    MatchTick.tick_number == state.tick,
                    MatchTick.state_hash == digest,
                )
            )
            if proven is None:
                stage_proof = await session.scalar(
                    select(MatchStage.match_id)
                    .where(MatchStage.match_id == match.id, MatchStage.state_hash == digest)
                    .limit(1)
                )
                proven = stage_proof
            if proven is None:
                raise ValueError("No immutable checkpoint proof")
            tick = None
            if state.phase == MatchPhase.HALF_TIME:
                kind = "match.second_half_start"
            elif state.phase == MatchPhase.EXTRA_BREAK:
                kind = "match.extra_start"
            elif state.phase == MatchPhase.EXTRA_HALF_TIME:
                kind = "match.extra_second_half_start"
            elif state.phase == MatchPhase.PENALTIES:
                assert state.knockout and state.knockout.shootout
                kind, tick = "match.penalty", len(state.knockout.shootout.kicks) + 1
            elif state.phase in {
                MatchPhase.FIRST_HALF,
                MatchPhase.SECOND_HALF,
                MatchPhase.EXTRA_FIRST_HALF,
                MatchPhase.EXTRA_SECOND_HALF,
            }:
                kind, tick = "match.tick", state.tick + 1
            else:
                raise ValueError("Not a live continuation")
        except (ValueError, TypeError, KeyError, AssertionError):
            corrupt += 1
            continue
        rows = (
            await session.scalars(
                select(Job).where(
                    Job.payload["fixture_id"].astext == str(fixture.id),
                    Job.kind == kind,
                    Job.payload["tick_number"].astext == (str(tick) if tick is not None else None),
                )
            )
        ).all()
        if rows:
            if len(rows) != 1 or rows[0].status != "pending" or rows[0].due_at != match.next_due_at:
                conflicts += 1
            continue
        key = f"reconcile:v1:{fixture.id}:{kind}:{tick}:{digest}"
        await session.execute(
            insert(Job)
            .values(
                id=uuid5(fixture.id, key),
                dedupe_key=key,
                kind=kind,
                due_at=match.next_due_at,
                status="pending",
                attempts=0,
                payload={
                    "version": 1,
                    "calendar_version": CALENDAR_VERSION,
                    "season_id": str(fixture.season_id),
                    "fixture_id": str(fixture.id),
                    "tick_number": tick,
                },
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
        )
        restored += 1
    job.payload = {
        "version": 1,
        "after": str(fixtures[-1].id) if len(fixtures) == BATCH else None,
        "last_scan_at": at.isoformat(),
        "scanned": len(fixtures),
        "restored": restored,
        "corrupt": corrupt,
        "conflicts": conflicts,
    }
    job.due_at = at + timedelta(seconds=30)
