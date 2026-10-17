"""Cross-set promotion/relegation cohorts and persisted allocation draws.

Cohort rule (explicit orchestration choice): the requested season plus every
other set's latest season that is fully complete joins one joint rollover.
Sets without a completed season are skipped silently — nobody waits for an
unfinished season. With a single set the cohort holds one season and the
allocation matches the tier ladder (stayers keep their league, movers take
the single league of their destination tier).
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.application.game_actions import allocation_lock
from goaletto.application.match_runtime import NotReady
from goaletto.application.results import league_table
from goaletto.domain.league_allocation import (
    ALLOCATION_POLICY,
    AllocationResult,
    ClubAssignment,
    LeagueSnapshot,
    TierDraw,
    allocate,
)
from goaletto.domain.season_rollover import ROLLOVER_POLICY
from goaletto.domain.standings import LeagueStanding
from goaletto.domain.world import stable_seed
from goaletto.infrastructure.models import (
    CupBracket,
    CupTie,
    Fixture,
    FixturePlan,
    League,
    LeagueSet,
    MatchResult,
    Season,
    SeasonAllocationReceipt,
    SeasonClub,
    Standing,
)

RECEIPT_NAMESPACE = UUID("9e8b6a5c-4d3e-4f2a-8b1c-6d5e4f3a2b1c")


@dataclass(frozen=True)
class ReadySeason:
    season: Season
    leagues: list[League]
    tables: dict[int, tuple[LeagueStanding, ...]]
    last_finish: datetime


async def load_ready_season(session: AsyncSession, season: Season, at: datetime) -> ReadySeason:
    """Run every completion gate and load the frozen final tables."""
    require_utc(at)
    if season.status == "scheduled":
        raise NotReady("Season opening has not committed")
    if season.status not in {"active", "finished"}:
        raise ValueError("Season is not ready for rollover")
    if at < season.ends_at:
        raise NotReady("Season calendar has not ended")
    if await session.get(Season, uuid5(season.id, f"{ROLLOVER_POLICY}:next")) is not None:
        raise ValueError("Next season exists without a rollover receipt")
    if await session.get(CupBracket, season.id) is not None:
        cup_ties = (
            await session.scalars(
                select(CupTie).where(CupTie.season_id == season.id).with_for_update()
            )
        ).all()
        if len(cup_ties) != 49 or any(t.status != "decided" for t in cup_ties):
            raise NotReady("Cup has unfinished ties")
        cup_plans = (
            await session.scalars(
                select(FixturePlan.id).where(
                    FixturePlan.season_id == season.id, FixturePlan.competition == "cup"
                )
            )
        ).all()
        completed_cup = (
            await session.scalars(
                select(MatchResult.fixture_id).where(
                    MatchResult.season_id == season.id, MatchResult.competition == "cup"
                )
            )
        ).all()
        if len(cup_plans) != 97 or set(cup_plans) != set(completed_cup):
            raise NotReady("Cup has missing result receipts")
    unannounced = await session.scalar(
        select(FixturePlan.id)
        .where(
            FixturePlan.season_id == season.id,
            FixturePlan.competition == "league",
            FixturePlan.announced_at.is_(None),
        )
        .limit(1)
    )
    if unannounced is not None:
        raise NotReady("Season has unresolved mandatory tournament plans")
    fixtures = list(
        (
            await session.scalars(
                select(Fixture)
                .where(
                    Fixture.season_id == season.id,
                    Fixture.competition == "league",
                )
                .order_by(Fixture.id)
                .with_for_update()
            )
        ).all()
    )
    results = list(
        (
            await session.scalars(
                select(MatchResult).where(
                    MatchResult.season_id == season.id,
                    MatchResult.competition == "league",
                )
            )
        ).all()
    )
    result_by_fixture = {r.fixture_id: r for r in results}
    if (
        len(fixtures) != 450
        or len(results) != 450
        or any(
            f.status not in {"finished", "abandoned"}
            or f.actual_finish_at is None
            or f.actual_finish_at > at
            or f.id not in result_by_fixture
            for f in fixtures
        )
    ):
        raise NotReady("Season still has unfinished or unfinalized fixtures")
    for f in fixtures:
        r = result_by_fixture[f.id]
        if (r.home_club_id, r.away_club_id, r.league_id, r.actual_start_at, r.actual_finish_at) != (
            f.home_club_id,
            f.away_club_id,
            f.league_id,
            f.actual_start_at,
            f.actual_finish_at,
        ):
            raise ValueError("Result differs from frozen fixture")
    last_finish = max(cast(datetime, f.actual_finish_at) for f in fixtures)
    leagues = list(
        (
            await session.scalars(
                select(League)
                .where(League.league_set_id == season.league_set_id)
                .order_by(League.tier)
            )
        ).all()
    )
    memberships = list(
        (await session.scalars(select(SeasonClub).where(SeasonClub.season_id == season.id))).all()
    )
    if len(leagues) != 5 or len(memberships) != 50:
        raise ValueError("Invalid five-tier season membership")
    tables = {league.tier: await league_table(session, season.id, league.id) for league in leagues}
    if any(row.played != 18 for table in tables.values() for row in table):
        raise NotReady("Season standings are incomplete")
    by_id = {m.club_id: m for m in memberships}
    expected = {m.club_id: [0] * 7 for m in memberships}
    for result in results:
        for club_id, goals, against in (
            (result.home_club_id, result.home_goals, result.away_goals),
            (result.away_club_id, result.away_goals, result.home_goals),
        ):
            if club_id not in expected or by_id[club_id].league_id != result.league_id:
                raise ValueError("Result is outside historical membership")
            values = expected[club_id]
            values[0] += 1
            values[1] += goals > against
            values[2] += goals == against
            values[3] += goals < against
            values[4] += goals
            values[5] += against
            values[6] += 3 if goals > against else int(goals == against)
    stored = list(
        (
            await session.scalars(
                select(Standing).where(Standing.season_id == season.id).with_for_update()
            )
        ).all()
    )
    if len(stored) != 50 or any(
        expected[r.club_id]
        != [r.played, r.won, r.drawn, r.lost, r.goals_for, r.goals_against, r.points]
        for r in stored
    ):
        raise ValueError("Season standings do not reconcile with results")
    return ReadySeason(season=season, leagues=leagues, tables=tables, last_finish=last_finish)


async def ready_peers(session: AsyncSession, season: Season, at: datetime) -> list[ReadySeason]:
    """Collect every other set's completed latest season; unfinished sets wait out."""
    await allocation_lock(session)
    ready: list[ReadySeason] = []
    set_ids = [
        row
        for row in (await session.scalars(select(LeagueSet.id).order_by(LeagueSet.id))).all()
        if row != season.league_set_id
    ]
    for set_id in set_ids:
        peer = await session.scalar(
            select(Season)
            .where(Season.league_set_id == set_id)
            .order_by(Season.number.desc())
            .limit(1)
            .with_for_update()
        )
        if peer is None or peer.status not in {"active", "finished"}:
            continue
        try:
            ready.append(await load_ready_season(session, peer, at))
        except NotReady:
            continue
    ready.sort(key=lambda item: item.season.league_set_id.hex)
    return ready


async def allocation_for_season(
    session: AsyncSession, season_id: UUID
) -> tuple[AllocationResult, list[UUID]] | None:
    """Reload the joint draw when a cohort peer already persisted it.

    The first ready job of a cohort writes one receipt; later peer jobs must
    reuse its destinations instead of redrawing alone.
    """
    stored = await session.scalar(
        select(SeasonAllocationReceipt)
        .where(SeasonAllocationReceipt.season_ids.contains([str(season_id)]))
        .order_by(SeasonAllocationReceipt.created_at.desc())
        .limit(1)
    )
    if stored is None:
        return None
    return (
        AllocationResult(
            policy_version=stored.policy_version,
            cohort_key=stored.cohort_key,
            seed=stored.seed,
            assignments=tuple(_assignment_from_json(a) for a in stored.assignments),
            draws=tuple(_draw_from_json(d) for d in stored.draws),
        ),
        [UUID(value) for value in stored.season_ids],
    )


async def origin_seasons(
    session: AsyncSession, allocation: AllocationResult, season_ids: list[UUID]
) -> dict[UUID, UUID]:
    """Map every assigned club to its origin season via source league sets."""
    league_rows = await session.execute(
        select(League.id, League.league_set_id).where(
            League.id.in_({a.source_league_id for a in allocation.assignments})
        )
    )
    league_sets: dict[UUID, UUID] = {row[0]: row[1] for row in league_rows.all()}
    season_rows = await session.execute(
        select(Season.id, Season.league_set_id).where(Season.id.in_(season_ids))
    )
    season_sets: dict[UUID, UUID] = {row[0]: row[1] for row in season_rows.all()}
    set_season = {league_set: season_id for season_id, league_set in season_sets.items()}
    return {a.club_id: set_season[league_sets[a.source_league_id]] for a in allocation.assignments}


def cohort_key_for(season_ids: list[UUID]) -> str:
    digest = hashlib.sha256("|".join(sorted(s.hex for s in season_ids)).encode()).hexdigest()
    return f"cohort:{digest}"


def seed_for(cohort_key: str) -> int:
    return stable_seed(0, f"{ALLOCATION_POLICY}:{cohort_key}")


def _assignment_to_json(assignment: ClubAssignment) -> dict[str, Any]:
    return {
        "club_id": str(assignment.club_id),
        "source_league_id": str(assignment.source_league_id),
        "source_tier": assignment.source_tier,
        "source_place": assignment.source_place,
        "destination_league_id": str(assignment.destination_league_id),
        "destination_tier": assignment.destination_tier,
        "movement": assignment.movement,
    }


def _assignment_from_json(payload: dict[str, Any]) -> ClubAssignment:
    return ClubAssignment(
        club_id=UUID(payload["club_id"]),
        source_league_id=UUID(payload["source_league_id"]),
        source_tier=payload["source_tier"],
        source_place=payload["source_place"],
        destination_league_id=UUID(payload["destination_league_id"]),
        destination_tier=payload["destination_tier"],
        movement=payload["movement"],
    )


def _draw_to_json(draw: TierDraw) -> dict[str, Any]:
    return {
        "tier": draw.tier,
        "dest_league_ids": [str(league_id) for league_id in draw.dest_league_ids],
        "from_above": [str(club_id) for club_id in draw.from_above],
        "from_below": [str(club_id) for club_id in draw.from_below],
    }


def _draw_from_json(payload: dict[str, Any]) -> TierDraw:
    return TierDraw(
        tier=payload["tier"],
        dest_league_ids=tuple(UUID(league_id) for league_id in payload["dest_league_ids"]),
        from_above=tuple(UUID(club_id) for club_id in payload["from_above"]),
        from_below=tuple(UUID(club_id) for club_id in payload["from_below"]),
    )


async def allocate_cohort(
    session: AsyncSession, ready: list[ReadySeason], at: datetime
) -> AllocationResult:
    """Allocate every cohort club exactly once; replay never redraws."""
    require_utc(at)
    if not ready:
        raise ValueError("Allocation cohort must hold at least one season")
    await allocation_lock(session)
    season_ids = sorted((item.season.id for item in ready), key=lambda s: s.hex)
    cohort_key = cohort_key_for(season_ids)
    seed = seed_for(cohort_key)
    receipt_id = uuid5(RECEIPT_NAMESPACE, cohort_key)
    stored = await session.get(SeasonAllocationReceipt, receipt_id)
    if stored is not None:
        if (
            stored.cohort_key != cohort_key
            or stored.seed != seed
            or stored.policy_version != ALLOCATION_POLICY
            or sorted(stored.season_ids) != sorted(str(s) for s in season_ids)
        ):
            raise ValueError("Allocation receipt does not match cohort")
        return AllocationResult(
            policy_version=stored.policy_version,
            cohort_key=stored.cohort_key,
            seed=stored.seed,
            assignments=tuple(_assignment_from_json(a) for a in stored.assignments),
            draws=tuple(_draw_from_json(d) for d in stored.draws),
        )
    snapshots = tuple(
        LeagueSnapshot(
            cohort_key=cohort_key,
            season_id=item.season.id,
            league_id=league.id,
            league_set_id=item.season.league_set_id,
            tier=league.tier,
            order=tuple(row.club_id for row in item.tables[league.tier]),
        )
        for item in ready
        for league in item.leagues
    )
    result = allocate(snapshots, seed=seed)
    session.add(
        SeasonAllocationReceipt(
            id=receipt_id,
            cohort_key=cohort_key,
            seed=seed,
            policy_version=result.policy_version,
            season_ids=[str(s) for s in season_ids],
            assignments=[_assignment_to_json(a) for a in result.assignments],
            draws=[_draw_to_json(d) for d in result.draws],
            created_at=at,
        )
    )
    await session.flush()
    return result
