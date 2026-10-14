"""Atomic season rollover. PostgreSQL owns every transition and accepted draw."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from random import Random
from typing import Literal, cast
from uuid import UUID, uuid5

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import JourneyEvent, Origin, Surface, require_utc
from goaletto.application.finances import close_salary, salary_contract
from goaletto.application.game_actions import allocation_lock
from goaletto.application.market import cancel_auction, prohibit_player_trades
from goaletto.application.match_runtime import NotReady
from goaletto.application.persons import AppearanceExhausted, NewPerson, create_person
from goaletto.application.results import league_table
from goaletto.application.world_portraits import age_band
from goaletto.domain.calendar import (
    CALENDAR_VERSION,
    ScheduledFixture,
    ScheduledWork,
    fixture_work,
    league_calendar,
    season_finish_at,
)
from goaletto.domain.leagues import plan_season_movements
from goaletto.domain.player_season_plan import PlayerSeasonEntry, plan_player_season
from goaletto.domain.retirement import RetirementDecision, player_last_season, staff_last_season
from goaletto.domain.season_rollover import ROLLOVER_POLICY, rollover_kickoff, youth_profile
from goaletto.domain.training import POLICY_VERSION, PROGRESS_KEYS, SKILLS, initial_caps
from goaletto.domain.world import SKILL_NAMES, stable_seed
from goaletto.infrastructure.analytics import CoverageNotStarted, record_event
from goaletto.infrastructure.models import (
    AppearanceIdentity,
    AppearanceVariant,
    Auction,
    CandidateOffer,
    CandidatePool,
    Club,
    ClubLineup,
    Fixture,
    Job,
    League,
    LineupSlot,
    Manager,
    MatchResult,
    OutboxMessage,
    OwnershipPeriod,
    Person,
    PersonSeasonEvent,
    Player,
    PlayerDevelopment,
    Season,
    SeasonClub,
    SeasonRolloverReceipt,
    Staff,
    Standing,
)
from goaletto.portraits.whole import load_whole_catalog


@dataclass(frozen=True)
class RolloverResult:
    season_id: UUID
    next_season_id: UUID
    created: bool
    aged: int
    retired: int
    warned: int
    replacements: int
    staff_aged: int
    staff_retired: int
    staff_warned: int


async def _event(
    session: AsyncSession,
    name: str,
    person_id: UUID,
    season_id: UUID,
    at: datetime,
    club_id: UUID | None = None,
) -> None:
    operation = uuid5(person_id, f"{ROLLOVER_POLICY}:{season_id}:{name}")
    try:
        await record_event(
            session,
            JourneyEvent(
                event_id=uuid5(operation, "event"),
                name=name,
                occurred_at=at,
                recorded_at=at,
                origin=Origin.AUTOMATION
                if name == "bot_youth_replacement_created"
                else Origin.SYSTEM,
                operation_id=operation,
                effect_number=0,
                surface=Surface.LIFECYCLE,
                person_id=person_id,
                season_id=season_id,
                club_id=club_id,
            ),
        )
    except CoverageNotStarted:
        pass


def _person_event(
    person: Person,
    season_id: UUID,
    stage: str,
    at: datetime,
    *,
    decision: RetirementDecision | None = None,
    related: UUID | None = None,
) -> PersonSeasonEvent:
    return PersonSeasonEvent(
        id=uuid5(person.id, f"{ROLLOVER_POLICY}:{season_id}:{stage}"),
        person_id=person.id,
        season_id=season_id,
        stage=stage,
        age=person.age,
        policy_version=decision.policy_version if decision else ROLLOVER_POLICY,
        probability_bps=decision.probability_bps if decision else None,
        draw=decision.draw if decision else None,
        is_last_season=decision.is_last_season if decision else None,
        related_person_id=related,
        occurred_at=at,
    )


async def _notice(
    session: AsyncSession,
    person: Person,
    club_id: UUID | None,
    season_id: UUID,
    action: str,
    at: datetime,
    *,
    staff: bool = False,
) -> None:
    if club_id is None:
        return
    ownership = await session.scalar(
        select(OwnershipPeriod).where(
            OwnershipPeriod.club_id == club_id,
            OwnershipPeriod.ended_at.is_(None),
            OwnershipPeriod.expires_at > at,
        )
    )
    if ownership is None:
        return
    manager = await session.get(Manager, ownership.manager_id)
    if manager is None:
        raise ValueError("Ownership manager missing")
    key = f"lifecycle:v1:{person.id}:{season_id}:{action}:{ownership.id}"
    await session.execute(
        insert(OutboxMessage).values(
            id=uuid5(person.id, key),
            dedupe_key=key,
            chat_id=manager.telegram_id,
            due_at=at,
            status="pending",
            attempts=0,
            priority=80,
            payload={
                "version": 1,
                "kind": "staff_lifecycle" if staff else "player_lifecycle",
                "action": action,
                "person_id": str(person.id),
                "season_id": str(season_id),
                "club_id": str(club_id),
                "ownership_id": str(ownership.id),
                "player_name": person.name,
            },
        )
    )


async def rollover_season(session: AsyncSession, season_id: UUID, at: datetime) -> RolloverResult:
    require_utc(at)
    if not session.in_transaction() or not isinstance(season_id, UUID):
        raise ValueError("Rollover needs a transaction and season UUID")
    # Workers already hold their job lock: try-lock avoids job/allocation deadlocks.
    if not await allocation_lock(session, try_lock=True):
        raise NotReady("Season allocation is busy")
    season = await session.scalar(
        select(Season)
        .where(Season.id == season_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if season is None:
        raise ValueError("Season missing")
    next_id = uuid5(season.id, f"{ROLLOVER_POLICY}:next")
    receipt = await session.get(SeasonRolloverReceipt, season.id)
    if receipt is not None:
        if (
            season.status != "rolled_over"
            or receipt.next_season_id != next_id
            or receipt.policy_version != ROLLOVER_POLICY
            or season.rollover_completed_at != receipt.completed_at
        ):
            raise ValueError("Rollover receipt does not match season")
        return RolloverResult(season.id, next_id, False, **receipt.counts)
    if season.status == "scheduled":
        raise NotReady("Season opening has not committed")
    if season.status not in {"active", "finished"}:
        raise ValueError("Season is not ready for rollover")
    if await session.get(Season, next_id) is not None:
        raise ValueError("Next season exists without a rollover receipt")
    # Lock every fixture so the completion proof and transition share one snapshot.
    fixtures = list(
        (
            await session.scalars(
                select(Fixture)
                .where(Fixture.season_id == season.id)
                .order_by(Fixture.id)
                .with_for_update()
            )
        ).all()
    )
    results = list(
        (await session.scalars(select(MatchResult).where(MatchResult.season_id == season.id))).all()
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
    kickoff = rollover_kickoff(season.starts_at, last_finish, at)
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
    by_id = {m.club_id: m for m in memberships}
    clubs = list(
        (
            await session.scalars(
                select(Club).where(Club.id.in_(by_id)).order_by(Club.id).with_for_update()
            )
        ).all()
    )
    if len(clubs) != 50 or any(c.league_id != by_id[c.id].league_id for c in clubs):
        raise ValueError("Current clubs disagree with season membership")
    tables = {league.tier: await league_table(session, season.id, league.id) for league in leagues}
    if any(row.played != 18 for table in tables.values() for row in table):
        raise NotReady("Season standings are incomplete")
    # The table must reconcile against every immutable receipt, not just its played counter.
    expected = {c.id: [0] * 7 for c in clubs}
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
    movement = plan_season_movements(
        {tier: [str(r.club_id) for r in table] for tier, table in tables.items()}
    )
    rows = list(
        (
            await session.execute(
                select(Player, Person)
                .join(Person)
                .where(
                    (Player.club_id.in_(by_id))
                    | (
                        Player.club_id.is_(None)
                        & (Person.home_league_set_id == season.league_set_id)
                    ),
                    Player.status.in_(("active", "last_season")),
                )
                .order_by(Person.id)
                .with_for_update(of=(Player, Person))
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    owners = list(
        (
            await session.scalars(
                select(OwnershipPeriod).where(
                    OwnershipPeriod.club_id.in_(by_id),
                    OwnershipPeriod.ended_at.is_(None),
                    OwnershipPeriod.expires_at > at,
                )
            )
        ).all()
    )
    owned = {o.club_id for o in owners}
    retiring = [
        (p, person)
        for p, person in rows
        if p.status == "last_season" and p.last_season_id == season.id
    ]
    catalog = load_whole_catalog()
    approved_youth = [
        key
        for key, entry in catalog["variants"].items()
        if entry["bank"] == "players" and 17 <= entry.get("depicted_age", 0) <= 21
    ]
    variants = list(
        (
            await session.scalars(
                select(AppearanceVariant)
                .join(AppearanceIdentity)
                .where(
                    AppearanceIdentity.origin_bank == "players",
                    AppearanceVariant.bank == "players",
                    (AppearanceVariant.stage == "player-youth")
                    | AppearanceVariant.id.in_(approved_youth),
                )
                .order_by(AppearanceIdentity.id, AppearanceVariant.id)
            )
        ).all()
    )
    if any(p.club_id is not None and p.club_id not in owned for p, _ in retiring) and not variants:
        raise AppearanceExhausted("No approved youth portrait in reusable catalog")
    # Everything from here commits or rolls back as one unit.
    next_seed = stable_seed(season.seed, f"{ROLLOVER_POLICY}:{next_id}")
    session.add(
        Season(
            id=next_id,
            league_set_id=season.league_set_id,
            number=season.number + 1,
            status="scheduled",
            seed=next_seed,
            starts_at=kickoff,
            ends_at=season_finish_at(kickoff),
        )
    )
    await session.flush()
    # Remove references before retired players leave the roster (composite FK).
    retired_ids = {p.person_id for p, _ in retiring}
    saved_slots = list(
        (
            await session.scalars(
                select(LineupSlot)
                .where(LineupSlot.club_id.in_(by_id))
                .order_by(LineupSlot.club_id, LineupSlot.slot)
            )
        ).all()
    )
    await session.execute(delete(LineupSlot).where(LineupSlot.person_id.in_(retired_ids)))
    await session.flush()
    replacements: dict[UUID, Player] = {}
    counts = dict.fromkeys(
        (
            "aged",
            "retired",
            "warned",
            "replacements",
            "staff_aged",
            "staff_retired",
            "staff_warned",
        ),
        0,
    )
    age_variants = list(
        (
            await session.scalars(
                select(AppearanceVariant).where(AppearanceVariant.bank == "players")
            )
        ).all()
    )
    variant_by_band = {(v.identity_id, v.stage): v.id for v in age_variants if v.stage}
    for player, person in rows:
        if person.career != "player":
            raise ValueError("Player profile and person career disagree")
        draw = Random(stable_seed(next_seed, f"decision:{person.id}")).randrange(10000)
        plan = plan_player_season(
            PlayerSeasonEntry(
                person_id=person.id,
                appearance_identity_id=person.appearance_identity_id,
                completed_season_id=season.id,
                next_season_id=next_id,
                current_age=person.age,
                current_age_applied_for_season_id=person.age_applied_for_season_id,
                current_status=cast(Literal["active", "last_season", "retired"], player.status),
                current_last_season_id=player.last_season_id,
                current_retired_after_season_id=player.retired_after_season_id,
                draw=draw,
            )
        )
        club_id = player.club_id
        person.age = plan.next_age
        person.age_applied_for_season_id = plan.next_age_applied_for_season_id
        player.status = plan.next_status
        player.last_season_id = plan.next_last_season_id
        player.retired_after_season_id = plan.next_retired_after_season_id
        if plan.outcome == "retire_now":
            counts["retired"] += 1
            person.career = "retired"
            player.club_id = None
            session.add(_person_event(person, season.id, "player_retired", at))
            await session.flush()
            await prohibit_player_trades(session, person.id, at)
            await close_salary(session, person.id, at)
            await _notice(session, person, club_id, season.id, "retired", at)
            await _event(session, "player_retired", person.id, season.id, at)
            if club_id is not None and club_id not in owned:
                youth_id = uuid5(person.id, f"{ROLLOVER_POLICY}:{season.id}:youth")
                name, skills = youth_profile(next_seed, youth_id, player.position)
                youth = await create_person(
                    session,
                    NewPerson(
                        id=youth_id,
                        creation_key=f"rollover:{season.id}:youth:{person.id}",
                        name=name,
                        age=18,
                        career="player",
                        origin_bank="players",
                        eligible_variant_ids=tuple(v.id for v in variants),
                        home_league_set_id=season.league_set_id,
                    ),
                )
                youth.age_applied_for_season_id = next_id
                replacement = Player(
                    person_id=youth.id,
                    club_id=club_id,
                    position=player.position,
                    alternative_positions=list(player.alternative_positions),
                    **dict(zip(SKILL_NAMES, skills, strict=True)),
                    form=6,
                    experience=0,
                    morale=50,
                    status="active",
                )
                session.add(replacement)
                session.add(
                    PlayerDevelopment(
                        person_id=youth.id,
                        caps=initial_caps(youth.id, dict(zip(SKILLS, skills[:8], strict=True))),
                        progress=dict.fromkeys(PROGRESS_KEYS, 0),
                        policy_version=POLICY_VERSION,
                    )
                )
                session.add(_person_event(youth, next_id, "age_applied", at))
                session.add(
                    _person_event(
                        youth,
                        next_id,
                        "player_last_decision",
                        at,
                        decision=player_last_season(18, 0),
                    )
                )
                session.add(
                    _person_event(person, season.id, "youth_replacement", at, related=youth.id)
                )
                await session.flush()
                await salary_contract(session, youth.id, club_id, "player", at)
                await _event(
                    session, "bot_youth_replacement_created", youth.id, next_id, at, club_id
                )
                replacements[person.id] = replacement
                counts["replacements"] += 1
        else:
            counts["aged"] += 1
            variant = variant_by_band.get((person.appearance_identity_id, age_band(person.age)))
            if variant is not None:
                person.appearance_variant_id = variant
            session.add(_person_event(person, next_id, "age_applied", at))
            session.add(
                _person_event(person, next_id, "player_last_decision", at, decision=plan.decision)
            )
            await session.flush()
            if plan.outcome == "continue_last_season":
                counts["warned"] += 1
                await prohibit_player_trades(session, person.id, at)
                await _notice(session, person, club_id, next_id, "last_season", at)
                await _event(session, "player_last_season_announced", person.id, next_id, at)
    await _rollover_staff(session, season, next_id, next_seed, by_id, at, counts)
    # Retired candidate offers leave supply, but historical offers/persons remain.
    await session.execute(
        update(CandidateOffer)
        .where(
            CandidateOffer.status == "available",
            CandidateOffer.person_id.in_(select(Person.id).where(Person.career == "retired")),
        )
        .values(status="retired")
    )
    # Cancellation also prevents old-season listings crossing into the new calendar.
    auctions = list(
        (
            await session.scalars(
                select(Auction)
                .where(
                    Auction.league_set_id == season.league_set_id,
                    Auction.status == "open",
                )
                .order_by(Auction.person_id)
                .with_for_update()
            )
        ).all()
    )
    for auction in auctions:
        await cancel_auction(session, auction, "calendar_changed", at)
    # Reconstitute the pre-rollover slots as an input only, then repair retirement holes.
    for club in clubs:
        await session.execute(delete(LineupSlot).where(LineupSlot.club_id == club.id))
    await session.flush()
    # Retired IDs cannot be inserted through the composite FK; feed old picks to repair directly.
    await _install_lineups(session, clubs, saved_slots, retired_ids, replacements)
    calendars: list[ScheduledFixture] = []
    for league in leagues:
        league_clubs = [club for club in clubs if movement[str(club.id)] == league.tier]
        draw_order = sorted(league_clubs, key=lambda c: stable_seed(next_seed, f"tie:{c.id}"))
        for order, club in enumerate(draw_order, 1):
            club.league_id = league.id
            session.add(
                SeasonClub(
                    season_id=next_id,
                    club_id=club.id,
                    league_id=league.id,
                    league_set_id=season.league_set_id,
                    tie_break_order=order,
                )
            )
        calendars.extend(
            league_calendar(
                season_id=next_id,
                league_id=league.id,
                clubs=tuple(c.id for c in league_clubs),
                first_kickoff=kickoff,
                seed=next_seed,
            )
        )
    await session.flush()
    session.add_all([Standing(season_id=next_id, club_id=c.id) for c in clubs])
    for planned in calendars:
        session.add(
            Fixture(
                id=planned.id,
                season_id=next_id,
                league_id=planned.league_id,
                home_club_id=planned.home_club_id,
                away_club_id=planned.away_club_id,
                round_number=planned.round_number,
                scheduled_start_at=planned.kickoff,
                lineup_lock_at=planned.lineup_lock_at,
                status="scheduled",
            )
        )
    await session.flush()
    work = [job for f in calendars for job in fixture_work(f)] + [
        ScheduledWork(
            f"calendar:v1:season:{next_id}:open",
            "season.open",
            kickoff - timedelta(minutes=5, seconds=1),
        ),
        ScheduledWork(
            f"calendar:v1:season:{next_id}:rollover",
            "season.rollover",
            season_finish_at(kickoff) + timedelta(seconds=1),
        ),
    ]
    job_rows = [
        {
            "id": uuid5(next_id, j.dedupe_key),
            "dedupe_key": j.dedupe_key,
            "kind": j.kind,
            "due_at": j.due_at,
            "status": "pending",
            "attempts": 0,
            "last_error": None,
            "payload": {
                "version": 1,
                "calendar_version": CALENDAR_VERSION,
                "season_id": str(next_id),
                "fixture_id": str(j.fixture_id) if j.fixture_id else None,
                "tick_number": j.tick_number,
            },
        }
        for j in work
    ]
    for offset in range(0, len(job_rows), 500):
        await session.execute(insert(Job), job_rows[offset : offset + 500])
    # Replenish only pools that were opened already; rollover does not create an
    # unused staff market. The first set owns free-candidate aging, not availability.
    from goaletto.application.candidates import replenish_pool

    pools = list((await session.scalars(select(CandidatePool))).all())
    for pool in pools:
        await replenish_pool(session, pool.kind, pool.profile, at)
    # Terminal old work cannot start again; finance jobs are club-period work and continue.
    await session.execute(
        update(Job)
        .where(
            Job.payload["season_id"].astext == str(season.id),
            Job.status.in_(("pending", "held")),
        )
        .values(status="done", last_error=None)
    )
    season.status, season.rollover_completed_at = "rolled_over", at
    session.add(
        SeasonRolloverReceipt(
            season_id=season.id,
            next_season_id=next_id,
            policy_version=ROLLOVER_POLICY,
            completed_at=at,
            counts=counts,
            movements={str(c.id): str(c.league_id) for c in clubs},
        )
    )
    await session.flush()
    return RolloverResult(season.id, next_id, True, **counts)


async def _install_lineups(
    session: AsyncSession,
    clubs: list[Club],
    old_slots: list[LineupSlot],
    retired: set[UUID],
    replacements: dict[UUID, Player],
) -> None:
    """Replace only retired picks; all other slots/tactics stay fixed."""
    # Detached old slot objects are inputs, never re-added with invalid FK references.
    for club in clubs:
        old = [s for s in old_slots if s.club_id == club.id]
        lineup = await session.get(ClubLineup, club.id)
        if lineup is None:
            raise ValueError("Lineup missing")
        roster = list(
            (
                await session.scalars(
                    select(Player)
                    .where(Player.club_id == club.id, Player.status.in_(("active", "last_season")))
                    .order_by(Player.person_id)
                )
            ).all()
        )
        remaining = {p.person_id: p for p in roster}
        starters = {s.person_id for s in old if s.slot <= 11 and s.person_id not in retired}
        used = set(starters)
        picks = []
        for slot in old:
            if slot.person_id not in retired:
                if slot.slot <= 11 or slot.person_id not in used:
                    picks.append((slot.person_id, slot.position))
                    used.add(slot.person_id)
                continue
            replacement = replacements.get(slot.person_id)
            if replacement is None and slot.slot <= 11:
                candidates = [
                    p
                    for p in roster
                    if p.person_id not in used
                    and (p.position == "GK" if slot.position == "GK" else p.position != "GK")
                ]
                bench = {s.person_id for s in old if s.slot > 11}
                candidates.sort(
                    key=lambda p: (
                        p.position != slot.position,
                        slot.position not in p.alternative_positions,
                        p.person_id not in bench,
                        p.person_id,
                    )
                )
                if not candidates:
                    raise NotReady("Next season roster lacks an eligible starter")
                replacement = candidates[0]
            if replacement is not None:
                picks.append((replacement.person_id, slot.position))
                used.add(replacement.person_id)
        if len(picks) < 11 or len({pid for pid, _ in picks}) != len(picks):
            raise NotReady("Next season needs a valid saved XI")
        if sum(remaining[pid].position == "GK" for pid, _ in picks[:11]) != 1:
            raise NotReady("Next season needs a goalkeeper")
        session.add_all(
            [
                LineupSlot(club_id=club.id, slot=i, person_id=pid, position=pos)
                for i, (pid, pos) in enumerate(picks, 1)
            ]
        )
        if picks != [(s.person_id, s.position) for s in old]:
            lineup.version += 1
    await session.flush()


async def _rollover_staff(
    session: AsyncSession,
    season: Season,
    next_id: UUID,
    seed: int,
    memberships: dict[UUID, SeasonClub],
    at: datetime,
    counts: dict[str, int],
) -> None:
    rows = (
        await session.execute(
            select(Staff, Person)
            .join(Person)
            .where(
                (Staff.club_id.in_(memberships))
                | ((Staff.club_id.is_(None)) & (Person.home_league_set_id == season.league_set_id)),
                Staff.status.in_(("active", "last_season")),
            )
            .order_by(Person.id)
            .with_for_update(of=(Staff, Person))
            .execution_options(populate_existing=True)
        )
    ).all()
    variants = (
        await session.scalars(
            select(AppearanceVariant)
            .where(AppearanceVariant.bank == "staff")
            .order_by(AppearanceVariant.id)
        )
    ).all()
    by_stage = {(v.identity_id, v.stage): v.id for v in variants if v.stage}
    for worker, person in rows:
        if person.career != "staff" or person.age_applied_for_season_id != season.id:
            raise ValueError("Staff calendar does not match the completed season")
        club_id = worker.club_id
        if worker.status == "last_season":
            if worker.last_season_id != season.id:
                raise ValueError("Staff warning is outside the completed season")
            worker.status, worker.retired_after_season_id = "retired", season.id
            worker.club_id, worker.role = None, None
            person.career = "retired"
            session.add(_person_event(person, season.id, "staff_retired", at))
            await session.flush()
            await close_salary(session, person.id, at)
            await _notice(session, person, club_id, season.id, "retired", at, staff=True)
            counts["staff_retired"] += 1
        else:
            person.age += 1
            person.age_applied_for_season_id = next_id
            decision = staff_last_season(
                person.age,
                Random(stable_seed(seed, f"staff-decision:{person.id}")).randrange(10000),
            )
            session.add(_person_event(person, next_id, "age_applied", at))
            session.add(
                _person_event(person, next_id, "staff_last_decision", at, decision=decision)
            )
            stage = (
                "staff-early"
                if person.age <= 44
                else "staff-middle"
                if person.age <= 59
                else "staff-senior"
            )
            variant = by_stage.get((person.appearance_identity_id, stage))
            if variant is not None:
                person.appearance_variant_id = variant
            counts["staff_aged"] += 1
            if decision.is_last_season:
                worker.status, worker.last_season_id = "last_season", next_id
                await _notice(session, person, club_id, next_id, "last_season", at, staff=True)
                counts["staff_warned"] += 1
    await session.flush()
