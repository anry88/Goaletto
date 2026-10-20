"""Prepare or atomically initialize a world. Does not start matches or claim clubs."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.finance_seed import opening_rows
from goaletto.application.world_portraits import assign_world_portraits
from goaletto.domain.calendar import (
    ScheduledFixture,
    ScheduledWork,
    league_calendar,
    next_season_kickoff,
)
from goaletto.domain.game_cycle import CYCLE_CALENDAR, midnight, season_end, season_epoch
from goaletto.domain.nationalities import nationality
from goaletto.domain.training import POLICY_VERSION, PROGRESS_KEYS, SKILLS, initial_caps
from goaletto.domain.world import SKILL_NAMES, WORLD_POLICY, WorldOptions, WorldPlan, build_world
from goaletto.infrastructure.locks import APPEARANCE_ALLOCATION_LOCK
from goaletto.infrastructure.models import (
    Base,
    Club,
    ClubLineup,
    FinanceAccount,
    Job,
    League,
    LeagueSet,
    LedgerEntry,
    LineupSlot,
    Person,
    PersonSeasonEvent,
    Player,
    PlayerDevelopment,
    SalaryContract,
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
    from goaletto.application.calendar_planning import season_work

    work = season_work(plan.season_id, options.first_kickoff)
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
        "held_jobs": len(world.work) + 50 + 1,
        "calendar_policy": CYCLE_CALENDAR,
        "first_kickoff": world.plan.options.first_kickoff.isoformat(),
        "season_finish": season_end(world.plan.options.first_kickoff).isoformat(),
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
    replay_from: datetime | None = None,
    season_number: int | None = None,
) -> SeedResult:
    """Caller owns the transaction; receipt is committed with all 1000 reservations."""
    if not session.in_transaction():
        raise ValueError("World initialization requires a caller-owned transaction")
    if season_number is not None and (type(season_number) is not int or season_number < 1):
        raise ValueError("Initial season number must be positive")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": APPEARANCE_ALLOCATION_LOCK}
    )
    receipt = await session.get(WorldSeedReceipt, options.key)
    if receipt is not None:
        if season_number is not None:
            saved = await session.get(Season, receipt.season_id)
            if saved is None or saved.number != season_number:
                raise ValueError("World key has another frozen season number")
        expected = (
            options
            if "naming_policy" in receipt.config
            else replace(options, naming_policy="legacy-v1")
        )
        if receipt.config_hash != expected.fingerprint:
            raise ValueError("World key already has a different seed configuration")
        from goaletto.application.coaches import ensure_starting_coaches

        await ensure_starting_coaches(session, initialized_at, league_set_id=receipt.league_set_id)
        return SeedResult(receipt.league_set_id, receipt.season_id, False)
    if initialized_at.tzinfo is None or initialized_at.utcoffset() != timedelta(0):
        raise ValueError("Initialization timestamp must be timezone-aware UTC")
    simulation_start = replay_from or initialized_at
    if replay_from is not None:
        from goaletto.application.analytics import require_utc

        require_utc(replay_from)
        if replay_from > initialized_at or replay_from > midnight(
            season_epoch(options.first_kickoff)
        ):
            raise ValueError("Historical bot replay must start before the season")
    if simulation_start >= options.first_kickoff - timedelta(minutes=5, seconds=1):
        raise ValueError("Choose a future kickoff with the preparation window still open")
    plan_id = build_world(options).league_set_id
    if await session.get(LeagueSet, plan_id) is not None:
        raise ValueError("World exists without its receipt; reconcile rather than reseed it")
    await register_catalog(session, root=root)
    # The reviewed bank is reusable across people/worlds (user decision 2026-10-13).
    prepared = prepare_world(options, root=root)
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
                "stadium_capacity": 5000,
                "supporters": 2500,
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
                "number": season_number or 1,
                "status": "scheduled",
                "seed": options.seed,
                "starts_at": options.first_kickoff,
                "ends_at": season_end(options.first_kickoff),
                "calendar_policy": CYCLE_CALENDAR,
                "cycle_epoch": season_epoch(options.first_kickoff),
                "calendar_enabled": False,
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
                "country_code": nationality(person.id),
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
    await write(
        PlayerDevelopment,
        [
            {
                "person_id": person.id,
                "caps": initial_caps(person.id, dict(zip(SKILLS, person.skills[:8], strict=True))),
                "progress": dict.fromkeys(PROGRESS_KEYS, 0),
                "policy_version": POLICY_VERSION,
            }
            for person in plan.players
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
    from goaletto.application.calendar_planning import add_plan, ensure_planner_job, job_row

    for fixture in prepared.fixtures:
        await add_plan(session, fixture, simulation_start)
    for work in prepared.work:
        await session.execute(insert(Job).values(**job_row(plan.season_id, work, "held")))
    await ensure_planner_job(session, plan.season_id, simulation_start, held=True)
    from goaletto.application.cup import initialize_season_cup

    await initialize_season_cup(session, plan.season_id, simulation_start)
    account_rows, ledger_rows, salary_rows, finance_jobs = opening_rows(plan, simulation_start)
    await write(FinanceAccount, account_rows)
    await write(LedgerEntry, ledger_rows)
    await write(SalaryContract, salary_rows)
    await write(Job, finance_jobs)
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
                    "naming_policy": options.naming_policy,
                    "calendar_policy": CYCLE_CALENDAR,
                    "season_number": season_number or 1,
                },
                "created_at": initialized_at,
            }
        ],
    )
    from goaletto.application.coaches import ensure_starting_coaches

    await session.flush()
    await ensure_starting_coaches(session, simulation_start, league_set_id=plan.league_set_id)
    from goaletto.application.sponsorship import sign_sponsor

    for league in plan.leagues:
        for seeded_club in league.clubs:
            club_row = await session.get(Club, seeded_club.id)
            if club_row is None:
                raise ValueError("Missing sponsor club")
            await sign_sponsor(
                session,
                club_row,
                plan.season_id,
                simulation_start,
                season_end(plan.options.first_kickoff),
                tier=league.tier,
            )
    return SeedResult(plan.league_set_id, plan.season_id, True)
