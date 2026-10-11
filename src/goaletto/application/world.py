"""Prepare or atomically initialize a world. Does not start matches or claim clubs."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.world_portraits import assign_world_portraits
from goaletto.domain.calendar import (
    CALENDAR_VERSION,
    ScheduledFixture,
    ScheduledWork,
    fixture_work,
    league_calendar,
    next_season_kickoff,
    season_finish_at,
)
from goaletto.domain.world import SKILL_NAMES, WORLD_POLICY, WorldOptions, WorldPlan, build_world
from goaletto.infrastructure.locks import APPEARANCE_ALLOCATION_LOCK
from goaletto.infrastructure.models import (
    Base,
    Club,
    ClubLineup,
    Fixture,
    Job,
    League,
    LeagueSet,
    LineupSlot,
    Person,
    PersonSeasonEvent,
    Player,
    Season,
    SeasonClub,
    Standing,
    WorldSeedReceipt,
)
from goaletto.infrastructure.portrait_catalog import register_catalog
from goaletto.portraits.whole import WholePortraitRecipe


@dataclass(frozen=True)
class PreparedWorld:
    plan: WorldPlan
    portraits: dict[UUID, WholePortraitRecipe]
    fixtures: tuple[ScheduledFixture, ...]
    work: tuple[ScheduledWork, ...]


def prepare_world(
    options: WorldOptions,
    *,
    root: Path | None = None,
    reserved: frozenset[str] = frozenset(),
) -> PreparedWorld:
    plan = build_world(options)
    portraits = assign_world_portraits(plan, root=root, reserved=reserved)
    fixtures = tuple(
        fixture
        for league in plan.leagues
        for fixture in league_calendar(
            season_id=plan.season_id,
            league_id=league.id,
            clubs=tuple(club.id for club in league.clubs),
            first_kickoff=options.first_kickoff,
            seed=options.seed,
        )
    )
    work = tuple(job for fixture in fixtures for job in fixture_work(fixture)) + (
        ScheduledWork(
            f"calendar:v1:season:{plan.season_id}:open",
            "season.open",
            options.first_kickoff - timedelta(minutes=5, seconds=1),
        ),
        ScheduledWork(
            f"calendar:v1:season:{plan.season_id}:rollover",
            "season.rollover",
            season_finish_at(options.first_kickoff) + timedelta(seconds=1),
        ),
    )
    return PreparedWorld(plan, portraits, fixtures, work)


def world_summary(world: PreparedWorld) -> dict[str, Any]:
    clubs = [club for league in world.plan.leagues for club in league.clubs]
    core_means = [sum(sum(p.skills[:8]) for p in c.players) / 160 for c in clubs]
    return {
        "policy": WORLD_POLICY,
        "key": world.plan.options.key,
        "config_hash": world.plan.options.fingerprint,
        "league_set_id": str(world.plan.league_set_id),
        "season_id": str(world.plan.season_id),
        "leagues": len(world.plan.leagues),
        "clubs": len(clubs),
        "players": len(world.plan.players),
        "unique_faces": len({recipe.identity_id for recipe in world.portraits.values()}),
        "fixtures": len(world.fixtures),
        "held_jobs": len(world.work),
        "first_kickoff": world.plan.options.first_kickoff.isoformat(),
        "season_finish": season_finish_at(world.plan.options.first_kickoff).isoformat(),
        "next_season_kickoff": next_season_kickoff(world.plan.options.first_kickoff).isoformat(),
        "last_season_players": sum(p.retirement.is_last_season for p in world.plan.players),
        "core_skill_mean_min": min(core_means),
        "core_skill_mean_max": max(core_means),
        "core_skill_spread": max(core_means) - min(core_means),
    }


@dataclass(frozen=True)
class SeedResult:
    league_set_id: UUID
    season_id: UUID
    created: bool


async def initialize_world(
    session: AsyncSession,
    options: WorldOptions,
    *,
    initialized_at: datetime,
    root: Path | None = None,
) -> SeedResult:
    """Caller owns the transaction; receipt is committed with all 1000 reservations."""
    if not session.in_transaction():
        raise ValueError("World initialization requires a caller-owned transaction")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": APPEARANCE_ALLOCATION_LOCK}
    )
    receipt = await session.get(WorldSeedReceipt, options.key)
    if receipt is not None:
        if receipt.config_hash != options.fingerprint:
            raise ValueError("World key already has a different seed configuration")
        return SeedResult(receipt.league_set_id, receipt.season_id, False)
    if initialized_at.tzinfo is None or initialized_at.utcoffset() != timedelta(0):
        raise ValueError("Initialization timestamp must be timezone-aware UTC")
    if initialized_at >= options.first_kickoff - timedelta(minutes=5, seconds=1):
        raise ValueError("Choose a future kickoff with the preparation window still open")
    plan_id = build_world(options).league_set_id
    if await session.get(LeagueSet, plan_id) is not None:
        raise ValueError("World exists without its receipt; reconcile rather than reseed it")
    await register_catalog(session, root=root)
    reserved = frozenset((await session.scalars(select(Person.appearance_identity_id))).all())
    prepared = prepare_world(options, root=root, reserved=reserved)
    plan = prepared.plan

    async def write(model: type[Base], rows: list[dict[str, Any]]) -> None:
        for offset in range(0, len(rows), 500):
            await session.execute(insert(model).values(rows[offset : offset + 500]))

    await write(LeagueSet, [{"id": plan.league_set_id, "name": f"Goaletto {options.key}"}])
    await write(
        League,
        [
            {"id": league.id, "league_set_id": plan.league_set_id, "tier": league.tier}
            for league in plan.leagues
        ],
    )
    clubs = [club for league in plan.leagues for club in league.clubs]
    await write(
        Club,
        [
            {
                "id": club.id,
                "league_id": club.league_id,
                "name": club.name,
                "owner_telegram_id": None,
                "budget": options.initial_budget,
                "kit_primary": club.kit_primary,
                "kit_secondary": club.kit_secondary,
                "kit_version": 1,
            }
            for club in clubs
        ],
    )
    await write(
        Season,
        [
            {
                "id": plan.season_id,
                "league_set_id": plan.league_set_id,
                "number": 1,
                "status": "scheduled",
                "seed": options.seed,
                "starts_at": options.first_kickoff,
                "ends_at": season_finish_at(options.first_kickoff),
                "rollover_completed_at": None,
            }
        ],
    )
    await write(
        Person,
        [
            {
                "id": person.id,
                "creation_key": f"world:{options.key}:person:{person.id}",
                "name": person.name,
                "age": person.age,
                "career": "player",
                "appearance_identity_id": prepared.portraits[person.id].identity_id,
                "appearance_variant_id": prepared.portraits[person.id].variant_id,
                "home_league_set_id": plan.league_set_id,
                "age_applied_for_season_id": plan.season_id,
            }
            for person in plan.players
        ],
    )
    await write(
        Player,
        [
            {
                "person_id": p.id,
                "club_id": p.club_id,
                "position": p.position,
                "alternative_positions": [],
                **dict(zip(SKILL_NAMES, p.skills, strict=True)),
                "form": 6,
                "experience": 0,
                "morale": 50,
                "status": "last_season" if p.retirement.is_last_season else "active",
                "last_season_id": plan.season_id if p.retirement.is_last_season else None,
                "retired_after_season_id": None,
                "injured_until": None,
            }
            for p in plan.players
        ],
    )
    events: list[dict[str, Any]] = []
    for person in plan.players:
        common = {
            "person_id": person.id,
            "season_id": plan.season_id,
            "age": person.age,
            "occurred_at": initialized_at,
            "related_person_id": None,
        }
        events.extend(
            [
                {
                    **common,
                    "id": uuid5(person.id, "season:1:age"),
                    "stage": "age_applied",
                    "policy_version": WORLD_POLICY,
                    "probability_bps": None,
                    "draw": None,
                    "is_last_season": None,
                },
                {
                    **common,
                    "id": uuid5(person.id, "season:1:decision"),
                    "stage": "player_last_decision",
                    "policy_version": person.retirement.policy_version,
                    "probability_bps": person.retirement.probability_bps,
                    "draw": person.retirement.draw,
                    "is_last_season": person.retirement.is_last_season,
                },
            ]
        )
    await write(PersonSeasonEvent, events)
    await write(
        ClubLineup,
        [
            {
                "club_id": club.id,
                "formation": "4-4-2",
                "version": 1,
                "tactics_version": 1,
                "tactics": {"mentality": "balanced", "pressing": False, "counterattack": False},
            }
            for club in clubs
        ],
    )
    await write(
        LineupSlot,
        [
            {
                "club_id": person.club_id,
                "slot": person.slot,
                "person_id": person.id,
                "position": person.position,
            }
            for person in plan.players
        ],
    )
    await write(
        SeasonClub,
        [
            {
                "season_id": plan.season_id,
                "club_id": club.id,
                "league_id": club.league_id,
                "league_set_id": plan.league_set_id,
                "tie_break_order": club.tie_break_order,
            }
            for club in clubs
        ],
    )
    await write(
        Standing,
        [
            {
                "season_id": plan.season_id,
                "club_id": club.id,
                "played": 0,
                "won": 0,
                "drawn": 0,
                "lost": 0,
                "goals_for": 0,
                "goals_against": 0,
                "points": 0,
            }
            for club in clubs
        ],
    )
    await write(
        Fixture,
        [
            {
                "id": f.id,
                "season_id": f.season_id,
                "league_id": f.league_id,
                "home_club_id": f.home_club_id,
                "away_club_id": f.away_club_id,
                "round_number": f.round_number,
                "scheduled_start_at": f.kickoff,
                "lineup_lock_at": f.lineup_lock_at,
                "actual_start_at": None,
                "actual_finish_at": None,
                "status": "scheduled",
            }
            for f in prepared.fixtures
        ],
    )
    await write(
        Job,
        [
            {
                "id": uuid5(plan.season_id, job.dedupe_key),
                "dedupe_key": job.dedupe_key,
                "kind": job.kind,
                "due_at": job.due_at,
                "status": "held",
                "attempts": 0,
                "last_error": None,
                "payload": {
                    "version": 1,
                    "calendar_version": CALENDAR_VERSION,
                    "season_id": str(plan.season_id),
                    "fixture_id": str(job.fixture_id) if job.fixture_id else None,
                    "tick_number": job.tick_number,
                },
            }
            for job in prepared.work
        ],
    )
    await write(
        WorldSeedReceipt,
        [
            {
                "key": options.key,
                "league_set_id": plan.league_set_id,
                "season_id": plan.season_id,
                "config_hash": options.fingerprint,
                "policy_version": WORLD_POLICY,
                "config": {
                    "seed": options.seed,
                    "first_kickoff": options.first_kickoff.isoformat(),
                    "initial_budget": options.initial_budget,
                },
                "created_at": initialized_at,
            }
        ],
    )
    return SeedResult(plan.league_set_id, plan.season_id, True)
