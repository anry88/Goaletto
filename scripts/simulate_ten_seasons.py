"""Accelerated local calibration using the real PostgreSQL application services.

Never starts polling, a sender or an HTTP service. The existing local application
DB is disposable only when unprotected. Match clocks advance through the pure
engine; lock/start/finalization, money, development and seasons persist normally.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import time
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import func, make_url, select, text, update

from goaletto.application import academy, candidates, scouting
from goaletto.application.club_view import get_owned_club
from goaletto.application.development import growth_budget
from goaletto.application.game_actions import Actor
from goaletto.application.lineup import LineupInput, load_lineup, save_lineup
from goaletto.application.match_runtime import activate_season
from goaletto.application.match_simulation import simulate_match
from goaletto.application.money import locked_accounts
from goaletto.application.onboarding import begin_game, claim_club
from goaletto.application.results import finalize_result, league_table
from goaletto.application.season_rollover import rollover_season
from goaletto.application.training import select_training, training_blocked
from goaletto.application.world import initialize_world
from goaletto.config import Settings
from goaletto.domain.academy import upgrade_cost
from goaletto.domain.development import SKILLS
from goaletto.domain.finances import scout_mission_fee
from goaletto.domain.game_cycle import midnight
from goaletto.domain.lineup import StarterPick
from goaletto.domain.match_codec import restore_state, state_document
from goaletto.domain.match_engine import start_match
from goaletto.domain.match_types import FieldSlot, MatchSkills, PlayerSnapshot, TeamSnapshot
from goaletto.domain.training import Focus, Intensity
from goaletto.domain.world import PRIMARY_SKILL as BASE_PRIMARY_SKILL
from goaletto.domain.world import WorldOptions
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.jobs import process_one_job
from goaletto.infrastructure.models import (
    Base,
    Club,
    Fixture,
    Job,
    League,
    LedgerEntry,
    Match,
    OwnershipPeriod,
    Person,
    Player,
    PlayerDevelopment,
    ScoutDiscovery,
    ScoutMission,
    Season,
    SeasonClub,
    SeasonRolloverReceipt,
    Staff,
)

PRIMARY_SKILL = {
    **BASE_PRIMARY_SKILL,
    "DM": "defending",
    "AMC": "playmaking",
    "AML": "winger",
    "AMR": "winger",
}

NAMESPACE = UUID("302767e7-f0a3-4aa7-80ac-e81785a88cb7")
STRATEGIES = ("bot", "maintenance", "lineup", "academy", "scout", "defence", "attack")
# Background capacity, polling/reconcile, per-tick scheduling and Telegram delivery
# are deliberately outside a gameplay calibration. All football still uses V4.
HELD = (
    "world.reserve",
    "world.catch_up",
    "calendar.materialize",
    "runtime.reconcile",
    "feedback.scan",
    "feedback.cleanup",
    "match.remind",
    "ownership.remind",
    "match.tick",
    "match.second_half_start",
    "match.extra_start",
    "match.extra_second_half_start",
    "match.penalty",
    "match.finalize_result",
    "season.rollover",
)


@dataclass
class Case:
    strategy: str
    club_id: UUID
    manager_id: UUID | None = None
    telegram_id: int = 0
    ownership_id: UUID | None = None


def local_target(settings: Settings, local_service: bool = False) -> None:
    url = make_url(settings.database_url.get_secret_value())
    port = 5433
    if local_service:
        marker = Path("/run/goaletto-calibration-local")
        if not marker.is_file() or len(marker.read_text().strip()) != 64:
            raise ValueError("Local service requires the verified Mac Docker launcher")
        port = 5432
    if (settings.environment, url.drivername, url.host, url.port, url.database) != (
        "development",
        "postgresql+asyncpg",
        "127.0.0.1",
        port,
        "goaletto",
    ):
        raise ValueError(
            "Calibration requires the existing local development Goaletto on 127.0.0.1:5433"
        )


def actor(case: Case, at: datetime, action: str) -> Actor:
    assert case.manager_id is not None
    key = uuid5(NAMESPACE, f"{case.club_id}:{at.isoformat()}:{action}")
    return Actor(case.manager_id, case.telegram_id, None, key, at, key.int % (2**62))


def strength(player: Player) -> float:
    return float(getattr(player, PRIMARY_SKILL[player.position]))


def pick_lineup(
    players: list[Any], at: datetime
) -> tuple[tuple[StarterPick, ...], tuple[UUID, ...]]:
    eligible = [p for p in players if p.injured_until is None or p.injured_until <= at]
    selected = []
    for position in ("GK", "DL", "DC", "DC", "DR", "ML", "MC", "MC", "MR", "FC", "FC"):
        possible = [p for p in eligible if (p.position == "GK") == (position == "GK")]
        if not possible:
            raise ValueError("Insufficient available positional coverage")

        def score(p, position=position):
            factor = (
                1
                if p.position == position
                else 0.85
                if position in p.alternative_positions
                else 0.6
            )
            return getattr(p, PRIMARY_SKILL[position]) * factor, strength(p), str(p.person_id)

        best = max(possible, key=score)
        selected.append(StarterPick(best.person_id, position))
        eligible.remove(best)
    bench = tuple(
        p.person_id for p in sorted(eligible, key=lambda p: (-strength(p), str(p.person_id)))[:9]
    )
    return tuple(selected), bench


def weak_player_stress(seasons: int = 10) -> list[dict]:
    from goaletto.domain.development import GrowthBudget
    from goaletto.domain.match_practice import CURRENT_POLICY, Competition, develop_match_practice
    from goaletto.domain.training import PROGRESS_KEYS, TrainingPlayer, train_player

    player = TrainingPlayer(
        UUID(int=71),
        17,
        "DC",
        dict.fromkeys(SKILLS, 3),
        dict.fromkeys(SKILLS, 20),
        dict.fromkeys(PROGRESS_KEYS, 0),
        6,
        0,
    )
    rows: list[dict] = []
    for year in range(seasons):
        player = replace(player, age=17 + year)
        budget = GrowthBudget(player.age)
        for day in range(80):
            # Dense upper-bound participation, including ET and friendlies. No injuries.
            if day >= 8 and (day - 8) % 4 in (0, 1, 3):
                competition = (Competition.CUP, Competition.CHAMPIONS, None, Competition.LEAGUE)[
                    (day - 8) % 4
                ]
                assert competition is not None
                result = develop_match_practice(
                    player,
                    competition,
                    120,
                    {"defence_coach": 20},
                    budget,
                    match_seed=f"weak-stress:{year}:{day}",
                    policy=CURRENT_POLICY,
                )
                player = replace(
                    player,
                    skills=result.skills,
                    progress=result.progress,
                    experience=result.experience,
                    form=result.form,
                )
                budget = result.growth.budget
            else:
                session_result = train_player(
                    player,
                    Focus.DEFENDING,
                    Intensity.HIGH,
                    seed=f"weak-stress:{year}:{day}",
                    trainer_skill=20,
                    head_skill=20,
                    physio_skill=20,
                    budget=budget,
                )
                player = replace(
                    player,
                    skills=session_result.skills,
                    progress=session_result.progress,
                    experience=session_result.experience,
                    form=session_result.form,
                    morale=session_result.morale,
                    leadership=session_result.leadership,
                    leadership_progress=session_result.leadership_progress,
                )
                budget = session_result.budget
        assert sum(budget.used[k] for k in SKILLS) <= 30000
        assert budget.used["training"] <= 20000 and budget.used["match"] <= 10000
        assert max(budget.used[k] for k in SKILLS) <= 15000
        assert max(player.skills.values()) <= 3 + (year + 1) * 1.5
        rows.append(
            {
                "season": year + 1,
                "age": player.age,
                "skills": player.skills,
                "progress": player.progress,
                "budget": budget.used,
            }
        )
    assert rows[min(1, len(rows) - 1)]["skills"]["defending"] < 20
    return rows


class Simulation:
    def __init__(self, database: Database, output: Path, seed: int, seasons: int, probes: int):
        self.db, self.output, self.seed, self.seasons, self.probes = (
            database,
            output,
            seed,
            seasons,
            probes,
        )
        self.cases: list[Case] = []
        self.rows: list[dict] = []
        self.history: list[dict] = []
        self.actions: Counter = Counter()
        self.rejections: Counter = Counter()
        self.matches = 0
        self.events = 0
        self.audit: list[dict] = []
        self.initial_teams: dict[str, TeamSnapshot] = {}
        self.started = time.monotonic()
        self.elapsed_before_resume = 0.0

    async def reset(self):
        async with self.db.transaction() as s:
            if await s.scalar(text("SELECT current_database()")) != "goaletto":
                raise ValueError("Wrong database")
            if await s.scalar(
                text("SELECT EXISTS(SELECT 1 FROM data_protection WHERE protected_at IS NOT NULL)")
            ):
                raise ValueError("Protected real game/payment data: reset forbidden")
            if (
                await s.scalar(text("SELECT version_num FROM alembic_version"))
                != "0049_stars_cosmetics"
            ):
                raise ValueError(
                    "Apply the current migrations to the existing local database first"
                )
            # No arbitrary host/DB setting can bypass the exact local_target guard.
            tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
            await s.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))

    async def initialize(self):
        at = datetime(2030, 1, 1, tzinfo=UTC)
        options = WorldOptions(
            key=f"calibration-{self.seed}",
            seed=self.seed,
            first_kickoff=at + timedelta(days=11, hours=18),
            initial_budget=600000,
        )
        async with self.db.transaction() as s:
            result = await initialize_world(s, options, initialized_at=at)
            await activate_season(s, result.season_id, at)
            clubs = (
                await s.scalars(select(Club).join(League).where(League.tier == 5).order_by(Club.id))
            ).all()
            # Rotate strategy assignment with seed instead of assigning the best club by hand.
            offset = self.seed % len(clubs)
            clubs = list(clubs[offset:]) + list(clubs[:offset])
            for index, strategy in enumerate(STRATEGIES):
                case = Case(strategy, clubs[index].id)
                if strategy != "bot":
                    entry = await begin_game(
                        s,
                        telegram_id=800000 + index,
                        update_id=100 + index,
                        payload=None,
                        at=at,
                        internal=True,
                    )
                    a = replace(
                        entry.actor, operation_id=uuid5(NAMESPACE, strategy), update_id=200 + index
                    )
                    owner = await claim_club(s, a, case.club_id)
                    case.manager_id, case.telegram_id, case.ownership_id = (
                        a.manager_id,
                        a.telegram_id,
                        owner.id,
                    )
                self.cases.append(case)
            return result.season_id, at

    async def players(self, s, club_id):
        return list(
            (
                await s.scalars(
                    select(Player)
                    .where(Player.club_id == club_id, Player.status.in_(("active", "last_season")))
                    .order_by(Player.person_id)
                )
            ).all()
        )

    async def attempt(self, s, case, at, label, call):
        try:
            async with s.begin_nested():
                result = await call()
            self.actions[case.strategy + ":" + label] += 1
            return result
        except ValueError as error:
            # Rejected actions roll back, but corrupt engine/service state is never swallowed.
            self.rejections[case.strategy + ":" + label + ":" + str(error)[:100]] += 1
            return None

    async def hire(self, s, case, at, role):
        employed = await s.scalar(
            select(Staff).where(
                Staff.club_id == case.club_id,
                Staff.role == role,
                Staff.status.in_(("active", "last_season")),
            )
        )
        if employed is not None:
            return
        screen = await candidates.load_candidates(
            s, actor(case, at, "staff:" + role), "staff", record=False
        )
        available = screen.available_money
        offers = [
            i for i in screen.items if i.offer.profile == role and i.offer.fee <= available - 150000
        ]
        if not offers:
            return
        item = max(offers, key=lambda i: (i.skills[0], -i.offer.fee, str(i.person.id)))
        a = actor(case, at, "hire:" + role)
        signed = await self.attempt(
            s,
            case,
            at,
            "hire:" + role,
            lambda: candidates.sign_candidate(
                s, a, case.ownership_id, item.person.id, item.offer.fee, at=at
            ),
        )
        if signed:
            self.audit.append(
                {
                    "strategy": case.strategy,
                    "at": at.isoformat(),
                    "action": "hire",
                    "role": role,
                    "skill": item.skills[0],
                    "fee": item.offer.fee,
                }
            )

    async def manage(self, s, case, at, offseason, first_day):
        if case.ownership_id is None:
            return
        owner = await s.get(OwnershipPeriod, case.ownership_id)
        if owner.ended_at is not None:
            return  # Bankruptcy is an observed outcome, never a free refill/reclaim.
        await get_owned_club(s, actor(case, at, "visit"))
        players = await self.players(s, case.club_id)
        account = (await locked_accounts(s, case.club_id))[case.club_id]
        if offseason and first_day:
            if case.strategy == "academy":
                club = await s.get(Club, case.club_id)
                cost = upgrade_cost(club.academy_level) if club.academy_level < 5 else None
                if cost and account.available >= cost + 180000:
                    a = actor(case, at, "academy-upgrade")
                    await self.attempt(
                        s,
                        case,
                        at,
                        "academy_upgrade",
                        lambda: academy.upgrade_academy(s, a, case.ownership_id),
                    )
            # Common survival policy: academy base intake, then affordable public replacements.
            screen = await academy.load_academy(s, actor(case, at, "academy"))
            target = 23 if case.strategy == "academy" else 20
            for item in sorted(
                screen.candidates, key=lambda c: (-c.skills[PRIMARY_SKILL[c.position]], c.slot)
            ):
                if screen.signed >= screen.sign_limit or len(players) >= target:
                    break
                a = actor(case, at, "academy-sign:" + str(item.slot))
                pid = await self.attempt(
                    s,
                    case,
                    at,
                    "academy_sign",
                    lambda a=a, item=item: academy.sign_candidate(
                        s, a, case.ownership_id, item.slot
                    ),
                )
                if pid:
                    screen = await academy.load_academy(
                        s, actor(case, at, "academy-read"), record=False
                    )
                    players = await self.players(s, case.club_id)
            # Fill actual shortages only; all fees and salaries pass through the real service.
            if len(players) < 18:
                market = await candidates.load_candidates(
                    s, actor(case, at, "replacements"), "player", record=False
                )
                for market_item in sorted(
                    market.items, key=lambda i: (i.offer.fee, str(i.person.id))
                ):
                    if len(players) >= 18:
                        break
                    account = (await locked_accounts(s, case.club_id))[case.club_id]
                    if market_item.offer.fee > account.available - 120000:
                        continue
                    a = actor(case, at, "replacement:" + str(market_item.person.id))
                    await self.attempt(
                        s,
                        case,
                        at,
                        "replacement",
                        lambda a=a, market_item=market_item: candidates.sign_candidate(
                            s,
                            a,
                            case.ownership_id,
                            market_item.person.id,
                            market_item.offer.fee,
                            at=at,
                        ),
                    )
                    players = await self.players(s, case.club_id)
        roles = {
            "defence": "defence_coach",
            "attack": "attack_coach",
            "scout": "scout",
            "academy": "defence_coach",
        }
        if first_day and case.strategy in roles:
            await self.hire(s, case, at, roles[case.strategy])
        if case.strategy == "scout":
            ready = (
                await s.scalars(
                    select(ScoutDiscovery)
                    .join(ScoutMission)
                    .where(
                        ScoutMission.club_id == case.club_id,
                        ScoutMission.status == "completed",
                        ScoutMission.expires_at > at,
                        ScoutDiscovery.signed_at.is_(None),
                        ScoutDiscovery.declined_at.is_(None),
                    )
                    .order_by(ScoutDiscovery.rank)
                )
            ).all()
            if ready:
                offers = []
                for offer in ready:
                    p = await s.get(Player, offer.person_id)
                    offers.append((strength(p), offer))
                for quality, offer in sorted(
                    offers, key=lambda pair: (-pair[0], str(pair[1].person_id))
                ):
                    account = (await locked_accounts(s, case.club_id))[case.club_id]
                    if len(players) < 25 and offer.fee <= account.available - 150000:
                        a = actor(case, at, "scout-sign:" + str(offer.person_id))
                        bought = await self.attempt(
                            s,
                            case,
                            at,
                            "scout_sign",
                            lambda a=a, offer=offer: scouting.sign_finding(
                                s, a, case.ownership_id, offer.person_id, offer.fee
                            ),
                        )
                        if bought:
                            self.audit.append(
                                {
                                    "strategy": "scout",
                                    "action": "sign",
                                    "quality": quality,
                                    "fee": offer.fee,
                                    "at": at.isoformat(),
                                }
                            )
                            break
            active = await s.scalar(
                select(ScoutMission.id).where(
                    ScoutMission.club_id == case.club_id, ScoutMission.status == "active"
                )
            )
            # One substantial paid assignment per season, not an infinite search loop.
            if first_day and active is None:
                scout = await scouting.employed_scout(s, case.club_id)
                if scout:
                    fee = scout_mission_fee(scout[1], 16)
                    account = (await locked_accounts(s, case.club_id))[case.club_id]
                    if fee < account.available - 250000:
                        weakest = min(players, key=lambda p: (strength(p), str(p.person_id)))
                        a = actor(case, at, "scout-search")
                        await self.attempt(
                            s,
                            case,
                            at,
                            "scout_search",
                            lambda: scouting.start_search(
                                s, a, case.ownership_id, scout[0].id, weakest.position, 16, fee
                            ),
                        )
        focus = {
            "defence": Focus.DEFENDING,
            "academy": Focus.DEFENDING,
            "attack": Focus.ATTACKING,
        }.get(case.strategy)
        if focus is not None and not await training_blocked(
            s, case.club_id, midnight(at.date()), at=at
        ):
            a = actor(case, at, "training")
            intensity = Intensity.HIGH if case.strategy == "attack" else Intensity.MEDIUM
            await self.attempt(
                s,
                case,
                at,
                "training",
                lambda: select_training(
                    s,
                    a,
                    ownership_id=case.ownership_id,
                    week=midnight(at.date()),
                    focus=focus,
                    intensity=intensity,
                    clock=lambda: at,
                ),
            )
        if case.strategy != "maintenance":
            await self.choose_lineup(s, case, at)

    async def choose_lineup(self, s, case, at):
        players = await self.players(s, case.club_id)
        try:
            picks, bench = pick_lineup(players, at)
        except ValueError:
            self.rejections[case.strategy + ":lineup:coverage"] += 1
            return
        view = await load_lineup(s, actor(case, at, "lineup-read"))
        if view.locked:
            return
        a = actor(case, at, "lineup-save")
        await self.attempt(
            s,
            case,
            at,
            "lineup",
            lambda: save_lineup(
                s,
                a,
                LineupInput(
                    picks,
                    bench,
                    "4-4-2",
                    picks[0].person_id,
                    view.tactic,
                    view.mentality,
                    view.version,
                ),
                clock=lambda: at,
            ),
        )

    async def fast_finish(self, s, fixture_id):
        fixture = await s.get(Fixture, fixture_id)
        match = await s.scalar(select(Match).where(Match.fixture_id == fixture_id))
        if match is None or fixture.status == "finished":
            return
        final, _ = simulate_match(restore_state(match.state))
        if final.phase.value != "full_time":
            raise ValueError("Engine did not finish match")
        penalties = (
            len(final.knockout.shootout.kicks) if final.knockout and final.knockout.shootout else 0
        )
        minutes = 33 if final.tick == 30 else 46 + penalties
        fixture.status, fixture.actual_finish_at = (
            "finished",
            fixture.actual_start_at + timedelta(minutes=minutes),
        )
        match.state, match.phase, match.completed_tick = (
            state_document(final),
            "full_time",
            final.tick,
        )
        match.home_goals, match.away_goals = final.home.stats.goals, final.away.stats.goals
        match.next_due_at = None
        await s.flush()
        if not await finalize_result(s, fixture_id, fixture.actual_finish_at):
            raise ValueError("Missing result receipt")
        await s.execute(
            update(Job)
            .where(Job.status == "pending", Job.payload["fixture_id"].astext == str(fixture_id))
            .values(status="done")
        )
        if self.matches % 100 == 0:
            before = [
                (await s.get(Club, c)).budget for c in (fixture.home_club_id, fixture.away_club_id)
            ]
            assert not await finalize_result(s, fixture_id, fixture.actual_finish_at)
            after = [
                (await s.get(Club, c)).budget for c in (fixture.home_club_id, fixture.away_club_id)
            ]
            assert before == after
        self.matches += 1

    async def jobs_until(self, s, boundary):
        await s.execute(
            update(Job).where(Job.status == "pending", Job.kind.in_(HELD)).values(status="held")
        )
        while True:
            job = await s.scalar(
                select(Job)
                .where(Job.status == "pending", Job.due_at < boundary)
                .order_by(Job.due_at, Job.id)
                .limit(1)
            )
            if job is None:
                return
            kind, at, fixture_id = job.kind, job.due_at, job.payload.get("fixture_id")
            if not await process_one_job(s, at=at):
                raise ValueError("Due job not processed")
            self.events += 1
            await s.flush()
            if job.status == "failed":
                raise ValueError(f"Failed service job: {kind}/{job.last_error}")
            if job.status == "pending" and job.due_at <= at:
                raise ValueError(f"Deferred service job cannot advance: {kind}")
            if kind == "match.start":
                await self.fast_finish(s, UUID(fixture_id))
            await s.execute(
                update(Job).where(Job.status == "pending", Job.kind.in_(HELD)).values(status="held")
            )

    async def team(self, s, case):
        players = await self.players(s, case.club_id)
        picks, bench = pick_lineup(players, datetime(2100, 1, 1, tzinfo=UTC))
        club = await s.get(Club, case.club_id)
        return TeamSnapshot(
            club_id=club.id,
            name=club.name,
            players=tuple(
                PlayerSnapshot(
                    p.person_id,
                    str(p.person_id),
                    p.position,
                    MatchSkills(*(getattr(p, k) for k in SKILLS)),
                    age=25,
                    form=6,
                    morale=50,
                )
                for p in players
            ),
            lineup=tuple(FieldSlot(p.person_id, p.slot_position) for p in picks),
            bench=bench,
        )

    def probe(self, strategy, team):
        # Paired home/away, same match seeds and a fixed starting bot opponent.
        opponent = self.initial_teams["bot"]

        # Canonical identities and match UUIDs give each strategy the same random shocks.
        def canonical(original, label):
            ordered = [slot.player_id for slot in original.lineup] + list(original.bench)
            ordered += [p.id for p in original.players if p.id not in ordered]
            ids = {key: uuid5(NAMESPACE, f"{label}:player:{i}") for i, key in enumerate(ordered)}
            return replace(
                original,
                club_id=uuid5(NAMESPACE, label),
                players=tuple(replace(p, id=ids[p.id]) for p in original.players),
                lineup=tuple(
                    replace(slot, player_id=ids[slot.player_id]) for slot in original.lineup
                ),
                bench=tuple(ids[key] for key in original.bench),
            )

        team, opponent = canonical(team, "probe-team"), canonical(opponent, "probe-opponent")
        totals: Counter[str] = Counter()
        for i in range(self.probes):
            for home in (True, False):
                first, second = (team, opponent) if home else (opponent, team)
                initial = start_match(
                    uuid5(NAMESPACE, f"probe:{i}:{home}"),
                    i + self.seed * 100,
                    first,
                    second,
                )
                final, _ = simulate_match(initial)
                a, b = (final.home, final.away) if home else (final.away, final.home)
                totals["points"] += (
                    3 if a.stats.goals > b.stats.goals else int(a.stats.goals == b.stats.goals)
                )
                totals["gf"] += a.stats.goals
                totals["ga"] += b.stats.goals
        return round(totals["points"] / max(1, 2 * self.probes), 4)

    async def snapshot(self, season_id, at, number, initial=False):
        async with self.db.transaction() as s:
            all_rows = (
                await s.execute(
                    select(Player, Person, PlayerDevelopment)
                    .join(Person, Person.id == Player.person_id)
                    .join(PlayerDevelopment, PlayerDevelopment.person_id == Player.person_id)
                )
            ).all()
            for player, person, dev in all_rows:
                skills = {k: getattr(player, k) for k in SKILLS}
                if any(not 1 <= skills[k] <= dev.caps[k] <= 20 for k in SKILLS):
                    raise ValueError("Potential cap violated")
                budget = growth_budget(dev, person.age)
                self.history.append(
                    {
                        "season": number,
                        "phase": "start" if initial else "end",
                        "person": str(person.id),
                        "club": str(player.club_id),
                        "age": person.age,
                        "status": player.status,
                        "skills": skills,
                        "progress": dict(dev.progress),
                        "caps": dict(dev.caps),
                        "budget": dict(budget.used),
                    }
                )
            for case in self.cases:
                club = await s.get(Club, case.club_id)
                players = [
                    r[0]
                    for r in all_rows
                    if r[0].club_id == case.club_id and r[0].status != "retired"
                ]
                assert club is not None
                membership = await s.get(SeasonClub, (season_id, case.club_id))
                assert membership is not None
                league = await s.get(League, membership.league_id)
                assert league is not None
                table = await league_table(s, season_id, league.id)
                row = next(r for r in table if r.club_id == case.club_id)
                try:
                    team = await self.team(s, case)
                    if initial:
                        self.initial_teams[case.strategy] = team
                    ppg = self.probe(case.strategy, team)
                except ValueError:
                    ppg = None
                ledger = (
                    await s.execute(
                        select(LedgerEntry.kind, func.sum(LedgerEntry.cash_delta))
                        .where(LedgerEntry.club_id == case.club_id)
                        .group_by(LedgerEntry.kind)
                    )
                ).all()
                owner = (
                    await s.get(OwnershipPeriod, case.ownership_id) if case.ownership_id else None
                )
                self.rows.append(
                    {
                        "season": 0 if initial else number,
                        "strategy": case.strategy,
                        "club": str(case.club_id),
                        "tier": league.tier,
                        "place": next(
                            i + 1 for i, r in enumerate(table) if r.club_id == case.club_id
                        ),
                        "points": row.points,
                        "played": row.played,
                        "goals_for": row.goals_for,
                        "goals_against": row.goals_against,
                        "budget": club.budget,
                        "academy": club.academy_level,
                        "roster": len(players),
                        "mean_primary": round(
                            sum(map(strength, players)) / max(1, len(players)), 4
                        ),
                        "mean_defending": round(
                            sum(p.defending for p in players) / max(1, len(players)), 4
                        ),
                        "mean_attacking": round(
                            sum(p.attacking for p in players) / max(1, len(players)), 4
                        ),
                        "probe_ppg": ppg,
                        "bankrupt": bool(club.bankrupt_at),
                        "ownership_ended": bool(owner and owner.ended_at),
                        "ledger": {k: int(v) for k, v in ledger},
                    }
                )
                if sum(int(v) for _, v in ledger) != club.budget:
                    raise ValueError("Ledger cash does not reconcile to club balance")
                if len(players) > 25:
                    raise ValueError("Roster cap violated")

    def growth_audit(self):
        by_person: dict[str, list[tuple[int, dict]]] = {}
        peak_two_year_gain = 0
        checked = 0
        for row in self.history:
            moment = 0 if row["phase"] == "start" else row["season"]
            for old_moment, old in by_person.get(row["person"], []):
                years = moment - old_moment
                if not 0 < years <= 2:
                    continue
                gains = [
                    (row["skills"][k] - old["skills"][k]) * 10000
                    + row["progress"][k]
                    - old["progress"][k]
                    for k in SKILLS
                ]
                if max(gains) > years * 15000 or sum(gains) > years * 30000:
                    raise ValueError("Persisted growth exceeds the age-season budget")
                if years == 2:
                    peak_two_year_gain = max(peak_two_year_gain, max(gains))
                checked += 1
            by_person.setdefault(row["person"], []).append((moment, row))
        return {
            "person_comparisons": checked,
            "max_two_season_skill_gain": peak_two_year_gain / 10000,
            "caps_and_budgets_valid": True,
            "ledger_reconciled": True,
        }

    def write(self, complete):
        self.output.mkdir(parents=True, exist_ok=True)
        data = {
            "complete": complete,
            "seed": self.seed,
            "seasons_requested": self.seasons,
            "seasons_completed": max((r["season"] for r in self.rows), default=0),
            "engine_matches": self.matches,
            "service_jobs": self.events,
            "elapsed_seconds": round(
                self.elapsed_before_resume + time.monotonic() - self.started, 2
            ),
            "strategies": self.rows,
            "probes_per_side": self.probes,
            "weak_player_upper_bound": weak_player_stress(self.seasons),
            "invariants": self.growth_audit(),
            "actions": dict(self.actions),
            "rejections": dict(self.rejections),
            "audit": self.audit,
            "method": "real DB services; accelerated pure match engine; no Telegram/per-tick IO",
            "limits": [
                "Shared world: strategies interact; no universal superiority claim.",
                "Common maintenance: base academy intake, affordable shortage replacements.",
                "Probe fixes condition/opponent/seed; measures squad skills and selection.",
                "No tick timing, live decisions, delivery, or host capacity acceptance.",
            ],
        }
        (self.output / "summary.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n"
        )
        with (self.output / "clubs.csv").open("w") as file:
            writer = csv.DictWriter(file, fieldnames=[k for k in self.rows[0] if k != "ledger"])
            writer.writeheader()
            writer.writerows({k: v for k, v in r.items() if k != "ledger"} for r in self.rows)
        (self.output / "players.json").write_text(
            json.dumps(self.history, ensure_ascii=False, default=str) + "\n"
        )
        lines = [
            "# Калибровка 10 сезонов",
            "",
            f"Завершено: {data['seasons_completed']}/{self.seasons}; "
            f"seed={self.seed}; матчей={self.matches}.",
            "",
            "| Сезон | Стратегия | Лига | Место | Очки | Навык | Защита | Атака | "
            "Баланс | Академия | Probe PPG |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in self.rows:
            lines.append(
                "| "
                + " | ".join(
                    str(r[k])
                    for k in (
                        "season",
                        "strategy",
                        "tier",
                        "place",
                        "points",
                        "mean_primary",
                        "mean_defending",
                        "mean_attacking",
                        "budget",
                        "academy",
                        "probe_ppg",
                    )
                )
                + " |"
            )
        lines += ["", "Ограничения:", *["- " + x for x in data["limits"]]]
        (self.output / "report.md").write_text("\n".join(lines) + "\n")

    async def resume(self):
        saved = json.loads((self.output / "summary.json").read_text())
        if saved["seed"] != self.seed:
            raise ValueError("Resume seed mismatch")
        self.rows = saved["strategies"]
        self.elapsed_before_resume = saved["elapsed_seconds"]
        self.history = json.loads((self.output / "players.json").read_text())
        self.actions.update(saved["actions"])
        self.rejections.update(saved["rejections"])
        self.audit = saved["audit"]
        self.matches, self.events = saved["engine_matches"], saved["service_jobs"]
        async with self.db.transaction() as s:
            season = await s.scalar(
                select(Season)
                .where(Season.status == "scheduled")
                .order_by(Season.number.desc())
                .limit(1)
            )
            if season is None or season.number != saved["seasons_completed"] + 1:
                raise ValueError("Only a committed season-boundary checkpoint can resume")
            receipt = await s.scalar(
                select(SeasonRolloverReceipt).where(
                    SeasonRolloverReceipt.next_season_id == season.id
                )
            )
            assert receipt is not None
            for row in self.rows[: len(STRATEGIES)]:
                case = Case(row["strategy"], UUID(row["club"]))
                owner = await s.scalar(
                    select(OwnershipPeriod)
                    .where(OwnershipPeriod.club_id == case.club_id)
                    .order_by(OwnershipPeriod.claimed_at)
                    .limit(1)
                )
                if owner is not None:
                    from goaletto.infrastructure.models import Manager

                    manager = await s.get(Manager, owner.manager_id)
                    assert manager is not None
                    case.manager_id, case.telegram_id, case.ownership_id = (
                        manager.id,
                        manager.telegram_id,
                        owner.id,
                    )
                self.cases.append(case)
            # Starting skill/position snapshots are stable despite later retirement/transfers.
            from types import SimpleNamespace

            for case in self.cases:
                values = [
                    r
                    for r in self.history
                    if r["phase"] == "start" and r["club"] == str(case.club_id)
                ]
                people = []
                for row in values:
                    p = await s.get(Player, UUID(row["person"]))
                    assert p is not None
                    people.append(
                        SimpleNamespace(
                            person_id=p.person_id,
                            position=p.position,
                            alternative_positions=p.alternative_positions,
                            injured_until=None,
                            **row["skills"],
                        )
                    )
                picks, bench = pick_lineup(people, receipt.completed_at)
                self.initial_teams[case.strategy] = TeamSnapshot(
                    club_id=case.club_id,
                    name=case.strategy,
                    players=tuple(
                        PlayerSnapshot(
                            p.person_id,
                            str(p.person_id),
                            p.position,
                            MatchSkills(*(getattr(p, k) for k in SKILLS)),
                            age=25,
                            form=6,
                            morale=50,
                        )
                        for p in people
                    ),
                    lineup=tuple(FieldSlot(p.person_id, p.slot_position) for p in picks),
                    bench=bench,
                )
            for row in self.rows[: len(STRATEGIES)]:
                row["probe_ppg"] = self.probe(row["strategy"], self.initial_teams[row["strategy"]])
            return season.id, receipt.completed_at, season.number

    async def refresh_reports(self):
        """Recompute every paired probe from saved season snapshots with identical draws."""
        from decimal import Decimal
        from types import SimpleNamespace

        async with self.db.transaction() as s:
            for row in self.rows:
                phase = "start" if row["season"] == 0 else "end"
                number = 1 if phase == "start" else row["season"]
                values = [
                    h
                    for h in self.history
                    if h["phase"] == phase
                    and h["season"] == number
                    and h["club"] == row["club"]
                    and h["status"] != "retired"
                ]
                people = []
                for h in values:
                    p = await s.get(Player, UUID(h["person"]))
                    assert p is not None
                    people.append(
                        SimpleNamespace(
                            person_id=p.person_id,
                            position=p.position,
                            alternative_positions=p.alternative_positions,
                            injured_until=None,
                            **h["skills"],
                        )
                    )
                try:
                    picks, bench = pick_lineup(people, datetime(2100, 1, 1, tzinfo=UTC))
                    team = TeamSnapshot(
                        club_id=UUID(row["club"]),
                        name=row["strategy"],
                        players=tuple(
                            PlayerSnapshot(
                                p.person_id,
                                str(p.person_id),
                                p.position,
                                MatchSkills(*(getattr(p, k) for k in SKILLS)),
                                age=25,
                                form=6,
                                morale=50,
                                alternatives=tuple(p.alternative_positions),
                            )
                            for p in people
                        ),
                        lineup=tuple(FieldSlot(p.person_id, p.slot_position) for p in picks),
                        bench=bench,
                    )
                    row["probe_ppg"] = self.probe(row["strategy"], team)
                except ValueError:
                    row["probe_ppg"] = None
                row["training_bps_mean"] = round(
                    sum(h["budget"]["training"] for h in values) / max(1, len(values)), 2
                )
                row["match_bps_mean"] = round(
                    sum(h["budget"]["match"] for h in values) / max(1, len(values)), 2
                )
                row["ledger"] = {k: int(Decimal(str(v))) for k, v in row["ledger"].items()}
                baseline = next(
                    r for r in self.rows if r["season"] == 0 and r["strategy"] == row["strategy"]
                )
                row["primary_change"] = round(row["mean_primary"] - baseline["mean_primary"], 4)
                row["defending_change"] = round(
                    row["mean_defending"] - baseline["mean_defending"], 4
                )
                row["attacking_change"] = round(
                    row["mean_attacking"] - baseline["mean_attacking"], 4
                )
                row["probe_change"] = (
                    round(row["probe_ppg"] - baseline["probe_ppg"], 4)
                    if row["probe_ppg"] is not None and baseline["probe_ppg"] is not None
                    else None
                )

    async def run(self, resume=False):
        from goaletto.application.calendar_planning import plan_horizon

        if resume:
            season_id, at, first = await self.resume()
        else:
            season_id, at = await self.initialize()
            await self.snapshot(season_id, at, 1, initial=True)
            first = 1
        for number in range(first, self.seasons + 1):
            async with self.db.transaction() as s:
                season = await s.get(Season, season_id)
                assert season is not None
                end = season.ends_at
            first_day = True
            while at < end:
                day = midnight(at.date())
                async with self.db.transaction() as s:
                    await plan_horizon(s, season_id, day)
                    await self.jobs_until(s, day + timedelta(minutes=1))
                    season = await s.get(Season, season_id)
                    assert season is not None and season.cycle_epoch is not None
                    off = season.status == "scheduled" and day < midnight(season.cycle_epoch)
                    for case in self.cases:
                        await self.manage(s, case, day + timedelta(minutes=1), off, first_day)
                    await self.jobs_until(s, min(end, day + timedelta(days=1)))
                first_day = False
                at = day + timedelta(days=1)
                if (at.date() - season.cycle_epoch).days % 8 == 0:
                    print(
                        json.dumps(
                            {
                                "season": number,
                                "day": at.date().isoformat(),
                                "matches": self.matches,
                                "elapsed_s": round(time.monotonic() - self.started),
                            }
                        ),
                        flush=True,
                    )
            await self.snapshot(season_id, at, number)
            self.write(False)
            print(
                json.dumps(
                    {
                        "season": number,
                        "matches": self.matches,
                        "elapsed_s": round(time.monotonic() - self.started),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            async with self.db.transaction() as s:
                unfinished = await s.scalar(
                    select(func.count())
                    .select_from(Fixture)
                    .where(Fixture.season_id == season_id, Fixture.status != "finished")
                )
                if unfinished:
                    raise ValueError(
                        f"{unfinished} unfinished real fixtures; cannot invent rollover"
                    )
                result = await rollover_season(s, season_id, at)
                season_id = result.next_season_id
                await activate_season(s, season_id, at)
        self.write(True)


async def run(args):
    settings = Settings()
    local_target(settings, args.local_service)
    if not args.reset_local_development and not args.resume:
        raise ValueError("Pass --reset-local-development for the disposable local application DB")
    db = Database(settings)
    try:
        # A dedicated connection holds a session lock across every commit in this run.
        async with db.engine.connect() as guard:
            locked = await guard.scalar(text("SELECT pg_try_advisory_lock(783254194)"))
            if not locked:
                raise ValueError("Another calibration is running")
            try:
                sim = Simulation(db, args.output, args.seed, args.seasons, args.probes)
                if not args.resume:
                    await sim.reset()
                if args.report_only:
                    if not args.resume:
                        raise ValueError("Report-only requires a committed checkpoint")
                    await sim.resume()
                    await sim.refresh_reports()
                    sim.write(sim.rows[-1]["season"] == args.seasons)
                else:
                    await sim.run(resume=args.resume)
            finally:
                await guard.execute(text("SELECT pg_advisory_unlock(783254194)"))
    finally:
        await db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset-local-development", action="store_true")
    parser.add_argument("--local-service", action="store_true")
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument(
        "--resume", action="store_true", help="Continue a completed season checkpoint"
    )
    parser.add_argument("--seasons", type=int, default=10)
    parser.add_argument("--seed", type=int, default=71)
    parser.add_argument("--probes", type=int, default=8)
    parser.add_argument("--output", type=Path, default=Path("work/calibration-10-seasons"))
    args = parser.parse_args()
    if not 1 <= args.seasons <= 100 or not 1 <= args.probes <= 100:
        parser.error("seasons/probes outside 1..100")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
