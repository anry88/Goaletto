"""Owned stadium planning, paid durable expansion, and pinned attendance basis."""

from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface, require_utc
from goaletto.application.finances import ranking_at
from goaletto.application.game_actions import (
    Actor,
    PermissionDenied,
    allocation_lock,
    owned_club,
    user_effect,
)
from goaletto.application.match_runtime import NotReady
from goaletto.application.money import debit_locked, locked_accounts
from goaletto.domain.finances import STADIUM_CAPACITIES, STADIUM_PRICES
from goaletto.domain.match_types import HomeCrowd
from goaletto.domain.stadium import (
    DEMAND_POLICY,
    EXPANSION_DAYS,
    Attendance,
    attendance,
    period_upkeep,
    stadium_level,
)
from goaletto.infrastructure.models import (
    Club,
    Fixture,
    Job,
    Match,
    MatchCrowd,
    MatchResult,
    StadiumExpansion,
)


async def crowd_for_fixture(
    session: AsyncSession, fixture: Fixture, home: Club, at: datetime
) -> Attendance:
    _, tier, place = await ranking_at(session, home.id, at, season_id=fixture.season_id)
    results = (
        await session.scalars(
            select(MatchResult)
            .where(
                MatchResult.season_id == fixture.season_id,
                MatchResult.actual_finish_at < at,
                (MatchResult.home_club_id == home.id) | (MatchResult.away_club_id == home.id),
            )
            .order_by(MatchResult.actual_finish_at.desc(), MatchResult.match_id.desc())
            .limit(5)
        )
    ).all()
    points = []
    for result in results:
        own, other = (
            (result.home_goals, result.away_goals)
            if result.home_club_id == home.id
            else (result.away_goals, result.home_goals)
        )
        points.append(3 if own > other else 1 if own == other else 0)
    return attendance(home.supporters, home.stadium_capacity, tier, place, tuple(points))


async def pin_crowd(
    session: AsyncSession,
    match: Match,
    demand: Attendance,
    at: datetime,
    *,
    neutral_basis: dict[str, Any] | None = None,
) -> None:
    session.add(
        MatchCrowd(
            match_id=match.id,
            policy_version="neutral-final-v1" if neutral_basis is not None else DEMAND_POLICY,
            locked_at=at,
            basis=neutral_basis if neutral_basis is not None else asdict(demand),
        )
    )
    await session.flush()


@dataclass(frozen=True)
class StadiumScreen:
    club_name: str
    ownership_id: UUID
    capacity: int
    supporters: int
    available: int
    arrears: int
    expansion: StadiumExpansion | None
    next_home_at: datetime | None
    projected: Attendance | None
    locked: HomeCrowd | None


async def load_stadium(
    session: AsyncSession, actor: Actor, *, record: bool = True
) -> StadiumScreen:
    ownership, club = await owned_club(session, actor)
    account = (await locked_accounts(session, club.id))[club.id]
    expansion = await session.scalar(
        select(StadiumExpansion).where(
            StadiumExpansion.club_id == club.id, StadiumExpansion.status == "queued"
        )
    )
    fixture = await session.scalar(
        select(Fixture)
        .where(
            Fixture.home_club_id == club.id,
            Fixture.neutral.is_(False),
            Fixture.status.in_(("scheduled", "locked", "live", "paused")),
        )
        .order_by(Fixture.scheduled_start_at, Fixture.id)
        .limit(1)
    )
    projected, locked = None, None
    if fixture:
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture.id))
        if match:
            from goaletto.domain.match_codec import restore_state

            locked = restore_state(match.starting_snapshot).crowd
        else:
            projected = await crowd_for_fixture(session, fixture, club, actor.at)
    if record:
        await user_effect(
            session, actor, "stadium_opened", Surface.CLUB, ownership=ownership, club_id=club.id
        )
    return StadiumScreen(
        club.name,
        ownership.id,
        club.stadium_capacity,
        club.supporters,
        account.available,
        account.record.arrears,
        expansion,
        fixture.scheduled_start_at if fixture else None,
        projected,
        locked,
    )


async def order_expansion(
    session: AsyncSession,
    actor: Actor,
    ownership_id: UUID,
    capacity: int,
    *,
    clock: Callable[[], datetime] | None = None,
) -> StadiumExpansion:
    ownership, club = await owned_club(session, actor)
    at = max(actor.at, clock() if clock else datetime.now(UTC))
    require_utc(at)
    if ownership.id != ownership_id or ownership.expires_at <= at:
        raise PermissionDenied("Expansion requires the current owner")
    previous = await session.scalar(
        select(StadiumExpansion).where(StadiumExpansion.operation_id == actor.operation_id)
    )
    if previous:
        if (
            previous.club_id != club.id
            or previous.ownership_id != ownership.id
            or previous.from_capacity != capacity
        ):
            raise ValueError("Expansion operation reused with different scope")
        return previous
    level = stadium_level(capacity)
    if capacity != club.stadium_capacity or level == 5:
        raise ValueError("Stadium quote is stale or maximum level")
    if await session.scalar(
        select(StadiumExpansion.id).where(
            StadiumExpansion.club_id == club.id, StadiumExpansion.status == "queued"
        )
    ):
        raise ValueError("Expansion is already running")
    account = (await locked_accounts(session, club.id))[club.id]
    expansion = StadiumExpansion(
        id=uuid5(club.id, f"stadium:v1:{actor.operation_id}"),
        club_id=club.id,
        ownership_id=ownership.id,
        operation_id=actor.operation_id,
        from_capacity=capacity,
        to_capacity=STADIUM_CAPACITIES[level],
        cost=STADIUM_PRICES[level],
        started_at=at,
        due_at=at + timedelta(days=EXPANSION_DAYS),
        status="queued",
    )
    await debit_locked(
        session,
        account,
        f"stadium:{expansion.id}",
        "stadium_upgrade",
        expansion.cost,
        at,
        details={"expansion_id": str(expansion.id), "from": capacity, "to": expansion.to_capacity},
    )
    session.add(expansion)
    await session.flush()
    await session.execute(
        insert(Job)
        .values(
            id=uuid5(expansion.id, "job"),
            dedupe_key=f"stadium:v1:{expansion.id}",
            kind="stadium.complete",
            payload={"version": 1, "expansion_id": str(expansion.id)},
            due_at=expansion.due_at,
            status="pending",
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )
    await user_effect(
        session,
        replace(actor, at=at),
        "stadium_expansion_ordered",
        Surface.CLUB,
        ownership=ownership,
        club_id=club.id,
    )
    return expansion


async def complete_expansion(session: AsyncSession, job: Job, at: datetime) -> None:
    require_utc(at)
    if (
        job.kind != "stadium.complete"
        or set(job.payload) != {"version", "expansion_id"}
        or type(job.payload["version"]) is not int
        or job.payload["version"] != 1
    ):
        raise ValueError("Invalid expansion job")
    if not await allocation_lock(session, try_lock=True):
        raise NotReady("Busy expansion")
    row = await session.scalar(
        select(StadiumExpansion)
        .where(StadiumExpansion.id == UUID(job.payload["expansion_id"]))
        .with_for_update()
    )
    if row is None or job.due_at != row.due_at:
        raise ValueError("Missing expansion or wrong deadline")
    if row.status == "finished":
        return
    if at < row.due_at:
        raise NotReady("Expansion not finished")
    club = await session.scalar(select(Club).where(Club.id == row.club_id).with_for_update())
    if (
        club is None
        or club.stadium_capacity != row.from_capacity
        or row.to_capacity != STADIUM_CAPACITIES[stadium_level(row.from_capacity)]
        or row.cost != STADIUM_PRICES[stadium_level(row.from_capacity)]
    ):
        raise ValueError("Expansion disagrees with paid level")
    club.stadium_capacity = row.to_capacity
    row.status, row.completed_at = "finished", at
    await session.flush()


async def upkeep_for_period(
    session: AsyncSession,
    club: Club,
    week: datetime,
    *,
    period_start: datetime,
) -> tuple[int, list[dict[str, str | int]]]:
    rows = (
        await session.scalars(
            select(StadiumExpansion)
            .where(StadiumExpansion.club_id == club.id)
            .order_by(StadiumExpansion.started_at, StadiumExpansion.id)
        )
    ).all()
    capacity = rows[0].from_capacity if rows else club.stadium_capacity
    finished = sorted(
        (row for row in rows if row.completed_at is not None),
        key=lambda row: row.completed_at or week,
    )
    start = period_start
    changes: list[tuple[datetime, int]] = []
    for row in finished:
        if row.completed_at is None:
            raise ValueError("Missing completed expansion timestamp")
        if row.completed_at <= start:
            capacity = row.to_capacity
        elif row.completed_at < week:
            changes.append((row.completed_at, row.to_capacity))
    return period_upkeep(capacity, start, week, tuple(changes))
