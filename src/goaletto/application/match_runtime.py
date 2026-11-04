"""Durable regulation jobs. Caller owns one PostgreSQL transaction, never delivery."""

import json
import math
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import JourneyEvent, Origin, Surface, require_utc
from goaletto.application.match_commentary import load_commentary
from goaletto.application.outbox_delivery import live_board_key_for, priority_for
from goaletto.application.results import finalize_result
from goaletto.domain.calendar import CALENDAR_VERSION
from goaletto.domain.clock import MatchClock, MatchPhase
from goaletto.domain.knockout_match import BREAK_PHASES, advance_penalty, start_extra_half
from goaletto.domain.match_codec import restore_state, state_document, state_hash
from goaletto.domain.match_engine import advance_tick, start_match, start_second_half
from goaletto.domain.match_types import (
    FORM_ONLY_ENGINE_VERSION,
    CommandKind,
    EnginePolicy,
    FieldSlot,
    HomeCrowd,
    Instruction,
    MatchCommand,
    MatchEvent,
    MatchSkills,
    MatchState,
    Mentality,
    PlayerSnapshot,
    Side,
    Tactic,
    TeamSnapshot,
)
from goaletto.domain.world import stable_seed
from goaletto.infrastructure.analytics import record_event, start_coverage
from goaletto.infrastructure.locks import APPEARANCE_ALLOCATION_LOCK
from goaletto.infrastructure.models import (
    AcceptedCommand,
    AppearanceIdentity,
    Club,
    ClubLineup,
    Fixture,
    Job,
    LineupSlot,
    Manager,
    Match,
    MatchRecovery,
    MatchStage,
    MatchTick,
    OutboxMessage,
    OwnershipPeriod,
    Person,
    Player,
    Season,
)
from goaletto.presentation.i18n import using_locale

RUNTIME_KINDS = frozenset(
    {
        "season.open",
        "match.remind",
        "match.lock_lineup",
        "match.start",
        "match.tick",
        "match.second_half_start",
        "match.finalize_result",
        "match.extra_start",
        "match.extra_second_half_start",
        "match.penalty",
    }
)


class NotReady(ValueError):
    """A predecessor, another worker's lock, or explicit pause prevents processing."""


def job_refs(job: Job) -> tuple[UUID, UUID | None, int | None]:
    p = job.payload
    if (
        set(p) != {"version", "calendar_version", "season_id", "fixture_id", "tick_number"}
        or type(p["version"]) is not int
        or p["version"] != 1
        or p["calendar_version"] != CALENDAR_VERSION
        or job.kind not in RUNTIME_KINDS | {"season.rollover"}
    ):
        raise ValueError("Unsupported calendar job")
    season = UUID(p["season_id"])
    fixture = UUID(p["fixture_id"]) if p["fixture_id"] is not None else None
    tick = p["tick_number"]
    if job.kind == "match.tick":
        if type(tick) is not int or not 1 <= tick <= 40:
            raise ValueError("Invalid tick job")
    elif job.kind == "match.penalty":
        if type(tick) is not int or tick < 1:
            raise ValueError("Invalid penalty job")
    elif tick is not None:
        raise ValueError("Unexpected tick")
    if (job.kind in {"season.open", "season.rollover"}) != (fixture is None):
        raise ValueError("Invalid fixture reference")
    return season, fixture, tick


def due_for(match: Match, fixture: Fixture, tick: int | None = None) -> datetime:
    if fixture.actual_start_at is None:
        raise NotReady("Kickoff has not committed")
    phase = restore_state(match.state).phase.value if match.phase == "paused" else match.phase
    if tick is not None:
        seconds = (
            MatchClock().tick_elapsed_seconds(tick)
            if tick <= 30
            else 2040 + (tick - 30) * 60
            if tick <= 35
            else 2400 + (tick - 35) * 60
        )
    elif phase == "extra_break":
        seconds = 2040
    elif phase == "extra_half_time":
        seconds = 2400
    elif phase == "penalties":
        state = restore_state(match.state)
        assert state.knockout and state.knockout.shootout
        seconds = 2760 + len(state.knockout.shootout.kicks) * 60
    else:
        seconds = 1080
    return fixture.actual_start_at + timedelta(seconds=seconds + match.paused_seconds)


def next_due(state: MatchState, match: Match, fixture: Fixture) -> datetime | None:
    if state.phase in {MatchPhase.FULL_TIME, MatchPhase.ABANDONED}:
        return None
    return due_for(
        match,
        fixture,
        None
        if state.phase in BREAK_PHASES | {MatchPhase.HALF_TIME, MatchPhase.PENALTIES}
        else state.tick + 1,
    )


async def schedule_knockout_work(
    session: AsyncSession, fixture: Fixture, match: Match, state: MatchState
) -> None:
    """Only the next durable continuation; retries preserve the same job key."""
    if match.next_due_at is None or state.tick < 30:
        return
    tick = None
    if state.phase in BREAK_PHASES:
        kind = (
            "match.extra_start"
            if state.phase == MatchPhase.EXTRA_BREAK
            else "match.extra_second_half_start"
        )
    elif state.phase == MatchPhase.PENALTIES:
        assert state.knockout and state.knockout.shootout
        kind, tick = "match.penalty", len(state.knockout.shootout.kicks) + 1
    else:
        kind, tick = "match.tick", state.tick + 1
    key = f"knockout:v1:{fixture.id}:{kind}:{tick}"
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


async def activate_season(session: AsyncSession, season_id: UUID, at: datetime) -> None:
    require_utc(at)
    if not session.in_transaction():
        raise ValueError("Runtime activation needs a transaction")
    from goaletto.application.game_actions import allocation_lock

    await allocation_lock(session)
    season = await session.scalar(select(Season).where(Season.id == season_id).with_for_update())
    if season is None or season.status not in {"scheduled", "active"}:
        raise ValueError("Season is not available for activation")
    from goaletto.application.calendar_planning import plan_horizon

    season.calendar_enabled = True
    await session.flush()
    await plan_horizon(session, season.id, at)
    await start_coverage(session, at)
    await session.execute(
        update(Job)
        .where(
            Job.status == "held",
            Job.kind.in_(RUNTIME_KINDS | {"season.rollover", "calendar.materialize"}),
            Job.payload["season_id"].astext == str(season_id),
        )
        .values(status="pending")
    )
    from sqlalchemy import String, cast

    from goaletto.infrastructure.models import SeasonClub

    await session.execute(
        update(Job)
        .where(
            Job.status == "held",
            Job.kind == "finance.cycle",
            Job.payload["club_id"].astext.in_(
                select(cast(SeasonClub.club_id, String)).where(SeasonClub.season_id == season_id)
            ),
        )
        .values(status="pending")
    )


async def team_snapshot(
    session: AsyncSession,
    club: Club,
    at: datetime,
    *,
    suspended: frozenset[UUID] = frozenset(),
    form_at: datetime | None = None,
) -> TeamSnapshot:
    lineup = await session.get(ClubLineup, club.id)
    if lineup is None:
        raise ValueError("Club has no saved lineup")
    rows = (
        await session.execute(
            select(Player, Person, AppearanceIdentity)
            .join(Person, Person.id == Player.person_id)
            .join(AppearanceIdentity, AppearanceIdentity.id == Person.appearance_identity_id)
            .where(Player.club_id == club.id, Player.status != "retired", Person.career == "player")
            .order_by(Player.person_id)
        )
    ).all()
    from goaletto.application.forms import settle_form

    for player, _, _ in rows:
        await settle_form(session, player, form_at if form_at is not None else at)
    players = tuple(
        PlayerSnapshot(
            person.id,
            person.name,
            player.position,
            MatchSkills(
                *(
                    getattr(player, k)
                    for k in (
                        "goalkeeping",
                        "defending",
                        "playmaking",
                        "winger",
                        "attacking",
                        "set_pieces",
                        "stamina",
                        "pace",
                    )
                )
            ),
            age=person.age,
            form=player.form,
            experience=player.experience,
            position_experience=tuple(sorted((player.position_experience or {}).items())),
            morale=player.morale,
            alternatives=tuple(player.alternative_positions),
            available=(player.injured_until is None or player.injured_until <= at)
            and person.id not in suspended,
            last_season=player.status == "last_season",
            appearance_identity_id=person.appearance_identity_id,
            appearance_variant_id=person.appearance_variant_id,
            catalog_version=appearance.catalog_version,
        )
        for player, person, appearance in rows
    )
    slots = (
        await session.scalars(
            select(LineupSlot).where(LineupSlot.club_id == club.id).order_by(LineupSlot.slot)
        )
    ).all()
    if not 11 <= len(slots) <= 20 or [s.slot for s in slots] != list(range(1, len(slots) + 1)):
        raise ValueError("Saved match squad must have XI and up to nine bench slots")
    if len(players) > 25:
        raise ValueError("Club roster exceeds twenty-five players")
    by_id = {p.id: p for p in players}
    suspended = suspended.intersection(by_id)
    if not {s.person_id for s in slots}.issubset(by_id):
        raise ValueError("Lineup contains a different roster")
    starters = [FieldSlot(s.person_id, s.position) for s in slots[:11]]
    bench = [s.person_id for s in slots[11:]]
    # Bring an existing unselected reserve into consideration for a suspension.
    # Stable saved-bench order first; no quality sorting or generated replacements.
    unavailable = frozenset(p.id for p in players if not p.available)
    if unavailable:
        selected = {s.person_id for s in slots}
        bench.extend(p.id for p in players if p.id not in selected and p.available)
    for index, slot in enumerate(starters):
        if by_id[slot.player_id].available:
            continue
        replacement = next(
            (pid for pid in bench if by_id[pid].available and by_id[pid].position == slot.position),
            None,
        )
        if replacement is None:
            replacement = next(
                (
                    pid
                    for pid in bench
                    if by_id[pid].available and slot.position in by_id[pid].alternatives
                ),
                None,
            )
        if replacement is None:
            replacement = (
                next(
                    (pid for pid in bench if by_id[pid].available and by_id[pid].position != "GK"),
                    None,
                )
                if unavailable
                and (
                    slot.position != "GK" or any(by_id[pid].position == "GK" for pid in unavailable)
                )
                else None
            )
        if replacement is None:
            raise ValueError("Not enough eligible players in the saved lineup")
        bench.remove(replacement)
        bench.append(slot.player_id)
        starters[index] = FieldSlot(replacement, slot.position)
    # Suspensions exclude both starters and substitutes. Injured bench handling
    # retains its existing behavior. Keep the saved lineup itself untouched.
    bench = [pid for pid in bench if pid not in suspended][:9]
    selected_ids = {s.player_id for s in starters} | set(bench)
    tactics = lineup.tactics
    if set(tactics) - {"tactic", "mentality", "pressing", "counterattack"}:
        raise ValueError("Unknown saved tactics field")
    if any(type(tactics[k]) is not bool for k in ("pressing", "counterattack") if k in tactics):
        raise ValueError("Invalid legacy tactics flag")
    if tactics.get("pressing") and tactics.get("counterattack"):
        raise ValueError("Conflicting saved tactics")
    from goaletto.application.coaches import coach_snapshot

    coach = await coach_snapshot(session, club.id)
    tactic = Tactic(
        tactics.get(
            "tactic",
            "pressing"
            if tactics.get("pressing")
            else "counter"
            if tactics.get("counterattack")
            else "normal",
        )
    )
    return TeamSnapshot(
        club.id,
        club.name,
        tuple(p for p in players if p.id in selected_ids),
        tuple(starters),
        tuple(bench),
        tactic=tactic if tactic in coach.abilities.tactics else coach.abilities.tactics[0],
        coach=coach,
        mentality=Mentality(tactics.get("mentality", "balanced")),
    )


def event_documents(events: tuple[MatchEvent, ...]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = json.loads(json.dumps([asdict(e) for e in events], default=str))
    for event in result:
        if event["assist_player_id"] is None:
            del event["assist_player_id"]
    return result


async def lifecycle_event(
    session: AsyncSession,
    fixture: Fixture,
    name: str,
    stage: str,
    at: datetime,
    command_id: UUID | None = None,
) -> None:
    operation = uuid5(fixture.id, f"runtime:v1:{stage}")
    await record_event(
        session,
        JourneyEvent(
            event_id=uuid5(operation, "event"),
            name=name,
            occurred_at=at,
            recorded_at=max(at, getattr(session, "info", {}).get("world_replay_recorded_at", at)),
            origin=Origin.SYSTEM,
            operation_id=operation,
            effect_number=0,
            surface=Surface.MATCH,
            fixture_id=fixture.id,
            command_id=command_id,
        ),
    )


async def stage_receipt(
    session: AsyncSession,
    match: Match,
    stage: str,
    at: datetime,
    due: datetime,
    events: tuple[MatchEvent, ...] = (),
) -> None:
    session.add(
        MatchStage(
            match_id=match.id,
            stage=stage,
            occurred_at=at,
            due_at=due,
            events=event_documents(events),
            state_hash=state_hash(restore_state(match.state)),
        )
    )


async def queue_notification(
    session: AsyncSession,
    fixture: Fixture,
    kind: str,
    stage: str,
    at: datetime,
    state: MatchState | None = None,
    *,
    resume_deadline: datetime | None = None,
) -> None:
    owners = (
        await session.execute(
            select(OwnershipPeriod, Manager)
            .join(Manager, Manager.id == OwnershipPeriod.manager_id)
            .where(
                OwnershipPeriod.club_id.in_((fixture.home_club_id, fixture.away_club_id)),
                OwnershipPeriod.ended_at.is_(None),
                OwnershipPeriod.expires_at > at,
            )
        )
    ).all()
    timelines: dict[str, list[str]] = {"ru": [], "en": []}
    if owners and state is not None:
        for code in timelines:
            with using_locale(code):
                timelines[code] = list(await load_commentary(session, state))
    from goaletto.application.cosmetics import vip_clubs

    badges = (
        await vip_clubs(session, {fixture.home_club_id, fixture.away_club_id}, at)
        if state is not None and owners
        else frozenset()
    )
    for ownership, manager in owners:
        payload: dict[str, Any] = {
            "version": 1,
            "kind": kind,
            "fixture_id": str(fixture.id),
            "ownership_id": str(ownership.id),
        }
        if state is not None:
            payload.update(
                match_id=str(state.match_id),
                tick=state.tick,
                phase=state.phase.value,
                home=state.home.snapshot.name,
                home_vip=fixture.home_club_id in badges,
                away_vip=fixture.away_club_id in badges,
                away=state.away.snapshot.name,
                home_goals=state.home.stats.goals,
                away_goals=state.away.stats.goals,
                game_minute=state.game_minute,
                commentary=timelines["ru"],
                commentary_by_locale=timelines,
                shots=[state.home.stats.shots, state.away.stats.shots],
                on_target=[state.home.stats.on_target, state.away.stats.on_target],
            )
            from goaletto.domain.match_performance import player_of_match

            best = player_of_match(state)
            if best:
                payload["player_of_match_name"] = next(
                    p.name
                    for team in (state.home, state.away)
                    for p in team.snapshot.players
                    if p.id == best
                )
            if kind == "match_result":
                from goaletto.infrastructure.models import SupporterChange

                audience = await session.scalar(
                    select(SupporterChange).where(
                        SupporterChange.match_id == state.match_id,
                        SupporterChange.club_id == ownership.club_id,
                    )
                )
                if audience and audience.after_count > audience.before_count:
                    payload["supporters_gain"] = audience.after_count - audience.before_count
            if state.knockout and state.knockout.shootout:
                from goaletto.domain.knockout import decide_shootout

                score = decide_shootout(state.knockout.shootout)
                payload["penalty_kicks"] = len(state.knockout.shootout.kicks)
                payload["penalties"] = (
                    [score.score_a, score.score_b]
                    if state.home.snapshot.club_id == state.knockout.tie.club_a_id
                    else [score.score_b, score.score_a]
                )
            if (
                state.knockout
                and state.phase == MatchPhase.FULL_TIME
                and state.knockout.segment != "leg1"
            ):
                from goaletto.domain.knockout_match import decision

                settled = decision(state)
                direct = state.home.snapshot.club_id == state.knockout.tie.club_a_id
                payload["aggregate"] = (
                    [settled.aggregate_a, settled.aggregate_b]
                    if direct
                    else [settled.aggregate_b, settled.aggregate_a]
                )
                payload["tie_winner_name"] = (
                    state.home.snapshot.name
                    if settled.winner == state.home.snapshot.club_id
                    else state.away.snapshot.name
                )
        if resume_deadline is not None:
            payload["resume_deadline"] = resume_deadline.isoformat()
        await session.execute(
            insert(OutboxMessage)
            .values(
                id=uuid5(fixture.id, f"outbox:{stage}:{ownership.id}"),
                dedupe_key=f"match:v1:{fixture.id}:{stage}:{ownership.id}",
                chat_id=manager.telegram_id,
                payload=payload,
                priority=priority_for(payload),
                live_board_key=live_board_key_for(payload),
                due_at=at,
                status="pending",
                attempts=0,
            )
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
        )


def domain_command(command: AcceptedCommand, state: MatchState, *, halftime: bool) -> MatchCommand:
    payload = command.payload
    if command.payload_version != 1 or command.target_tick_number is None:
        raise ValueError("Unsupported accepted command")
    side = Side.HOME if command.club_id == state.home.snapshot.club_id else Side.AWAY
    if command.club_id not in {state.home.snapshot.club_id, state.away.snapshot.club_id}:
        raise ValueError("Command club is outside this match")
    fields = {
        "substitution": {"outgoing_id", "incoming_id"},
        "tactics": {"tactic"},
        "mentality": {"mentality"},
        "instruction": {"player_id", "instruction"},
    }
    if command.kind not in fields or set(payload) != fields[command.kind]:
        raise ValueError("Unsupported closed command payload")
    kwargs: dict[str, Any] = {}
    for key, value in payload.items():
        kwargs[key] = (
            UUID(value)
            if key.endswith("_id")
            else Tactic(value)
            if key == "tactic"
            else Instruction(value)
            if key == "instruction"
            else Mentality(value)
        )
    kind = CommandKind.TACTIC if command.kind == "tactics" else CommandKind(command.kind)
    return MatchCommand(
        command.id, side, command.target_tick_number, kind, halftime=halftime, **kwargs
    )


async def apply_commands(
    session: AsyncSession,
    match: Match,
    fixture: Fixture,
    state: MatchState,
    tick: int,
    at: datetime,
    *,
    halftime: bool,
) -> tuple[MatchState, tuple[MatchEvent, ...]]:
    # Match is already locked. Match expiry's Ownership -> Club -> Command order
    # before locking command rows; never acquire the global allocation lock here.
    ownership_ids = (
        await session.scalars(
            select(AcceptedCommand.ownership_id)
            .where(AcceptedCommand.match_id == match.id, AcceptedCommand.status == "accepted")
            .distinct()
        )
    ).all()
    owners = (
        await session.scalars(
            select(OwnershipPeriod)
            .where(OwnershipPeriod.id.in_(ownership_ids))
            .order_by(OwnershipPeriod.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    clubs = (
        await session.scalars(
            select(Club)
            .where(Club.id.in_([o.club_id for o in owners]))
            .order_by(Club.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    managers = (
        await session.scalars(select(Manager).where(Manager.id.in_([o.manager_id for o in owners])))
    ).all()
    owner_by_id = {o.id: o for o in owners}
    club_by_id = {c.id: c for c in clubs}
    telegram_by_manager = {m.id: m.telegram_id for m in managers}
    rows = (
        await session.scalars(
            select(AcceptedCommand)
            .where(
                AcceptedCommand.match_id == match.id,
                AcceptedCommand.status == "accepted",
                AcceptedCommand.target_tick_number <= tick,
            )
            .order_by(
                AcceptedCommand.accepted_at,
                AcceptedCommand.telegram_update_id,
                AcceptedCommand.effect_number,
                AcceptedCommand.id,
            )
            .with_for_update()
        )
    ).all()
    # Commands accepted during halftime apply in its free window; ordinary tick16 commands
    # accepted after the second-half transition remain pending until tick16.
    if halftime:
        rows = [c for c in rows if c.accepted_at < due_for(match, fixture)]
    valid = []
    for row in rows:
        owner = owner_by_id.get(row.ownership_id)
        club = club_by_id.get(row.club_id)
        if (
            owner is None
            or club is None
            or owner.ended_at is not None
            or owner.expires_at <= at
            or owner.manager_id != row.manager_id
            or owner.club_id != row.club_id
            or club.owner_telegram_id != telegram_by_manager.get(row.manager_id)
        ):
            row.status = "cancelled"
            continue
        if row.accepted_at >= due_for(match, fixture, None if halftime else tick):
            row.status = "cancelled"
            continue
        try:
            valid.append(domain_command(row, state, halftime=halftime))
        except (ValueError, TypeError, KeyError):
            row.status = "cancelled"
    result = (
        (
            start_extra_half(state, tuple(valid))
            if state.phase in BREAK_PHASES
            else start_second_half(state, tuple(valid))
        )
        if halftime
        else advance_tick(state, tick, tuple(valid))
    )
    if not halftime:
        session.add(
            MatchTick(
                match_id=match.id,
                tick_number=tick,
                game_minute=result.state.game_minute,
                due_at=due_for(match, fixture, tick),
                committed_at=at,
                events=event_documents(result.events),
                state_hash=state_hash(result.state),
            )
        )
        await session.flush()  # Insert the referenced tick before any applied-command UPDATE.
    for row in rows:
        if row.id in result.accepted_commands:
            row.status = "applied"
            row.applied_at = at
            # Halftime is a stage, not a regulation tick; FK cannot point to the future tick16.
            row.applied_tick_number = None if halftime else tick
            await lifecycle_event(
                session,
                fixture,
                "match_command_applied",
                f"command:{row.id}:applied",
                at,
                command_id=row.id,
            )
        elif row.id in {c for c, _ in result.rejected_commands}:
            row.status = "cancelled"
    return result.state, result.events


async def handle_calendar_job(session: AsyncSession, job: Job, at: datetime) -> str | None:
    require_utc(at)
    if not session.in_transaction():
        raise ValueError("Runtime needs a caller-owned transaction")
    season_id, fixture_id, tick = job_refs(job)
    if job.kind == "season.open":
        season = await session.scalar(
            select(Season).where(Season.id == season_id).with_for_update()
        )
        if season is None or season.status not in {"scheduled", "active"}:
            raise ValueError("Invalid season opening")
        from goaletto.application.academy import release_intakes

        await release_intakes(session, season.id, at)
        season.status = "active"
        return None
    # The same short ownership gate used by admission/expiry/settings, acquired
    # before fixture locks. Try-lock defers workers instead of deadlocking an owner.
    if not await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(:key)"),
        {"key": APPEARANCE_ALLOCATION_LOCK},
    ):
        raise NotReady("Ownership allocation is busy")
    fixture = await session.scalar(
        select(Fixture)
        .where(Fixture.id == fixture_id)
        .with_for_update(skip_locked=True, key_share=True)
    )
    if fixture is None:
        if await session.scalar(select(Fixture.id).where(Fixture.id == fixture_id)) is None:
            raise ValueError("Unknown fixture")
        raise NotReady("Fixture unavailable or held by another worker")
    if fixture.season_id != season_id:
        raise ValueError("Job belongs to a different season")
    if job.kind == "match.finalize_result":
        if await finalize_result(session, fixture.id, at):
            await lifecycle_event(session, fixture, "result_finalized", "result", at)
        return None
    if fixture.status in {"finished", "abandoned", "cancelled"}:
        return None
    if fixture.status == "paused":
        raise NotReady("Explicitly paused")
    if job.kind == "match.remind":
        if at < fixture.scheduled_start_at:
            from goaletto.application.recovery import recover_player

            owned_clubs = select(Club.id).where(
                Club.owner_telegram_id.is_not(None),
                Club.id.in_((fixture.home_club_id, fixture.away_club_id)),
            )
            for pid in await session.scalars(
                select(Player.person_id)
                .where(
                    Player.club_id.in_(owned_clubs), Player.status.in_(("active", "last_season"))
                )
                .order_by(Player.person_id)
            ):
                await recover_player(session, pid, at)
            await queue_notification(session, fixture, "fixture_reminder", "reminder", at)
        return None
    match = await session.scalar(
        select(Match).where(Match.fixture_id == fixture.id).with_for_update()
    )
    if job.kind in {"match.lock_lineup", "match.start"}:
        other = await session.scalar(
            select(Fixture.id)
            .where(
                Fixture.id != fixture.id,
                Fixture.status.in_(("live", "paused")),
                (Fixture.home_club_id.in_((fixture.home_club_id, fixture.away_club_id)))
                | (Fixture.away_club_id.in_((fixture.home_club_id, fixture.away_club_id))),
            )
            .limit(1)
        )
        if other is not None:
            raise NotReady("A club is still playing another match")
    if job.kind == "match.lock_lineup":
        if at < fixture.lineup_lock_at:
            raise NotReady("Lineup deadline is not due")
        if match is not None:
            return None
        clubs = (
            await session.scalars(
                select(Club)
                .where(Club.id.in_((fixture.home_club_id, fixture.away_club_id)))
                .order_by(Club.id)
                .with_for_update()
            )
        ).all()
        by_id = {c.id: c for c in clubs}
        home, away = by_id[fixture.home_club_id], by_id[fixture.away_club_id]
        season = await session.get(Season, fixture.season_id)
        if season is None or season.status != "active":
            raise NotReady("Season has not opened")
        from goaletto.application.stadium import crowd_for_fixture, pin_crowd

        demand = await crowd_for_fixture(session, fixture, home, at)
        seed = stable_seed(season.seed, f"match:{fixture.id}")
        context = None
        if fixture.competition == "cup":
            from goaletto.application.cup import context_for_fixture

            context = await context_for_fixture(session, fixture)
        elif fixture.competition == "champions":
            from goaletto.application.champions import context_for_champions_fixture

            context = await context_for_champions_fixture(session, fixture)
        elif fixture.competition == "friendly":
            from goaletto.application.friendly import authenticate_fixture

            await authenticate_fixture(session, fixture)
        elif (fixture.competition or "league") != "league":
            raise ValueError("Unsupported competition runtime")
        if fixture.neutral:
            from goaletto.application.cup import pin_final_venue

            demand = await pin_final_venue(
                session, fixture, home, away, seed, at, competition=fixture.competition or "league"
            )
        from goaletto.application.discipline import exclusions, tournament_scope
        from goaletto.application.recovery import lock_energy, recover_player

        scope = await tournament_scope(session, fixture)
        if scope is not None:
            earlier = await session.scalar(
                select(Fixture.id)
                .where(
                    Fixture.season_id == fixture.season_id,
                    Fixture.competition == fixture.competition,
                    Fixture.status.in_(("scheduled", "live", "paused")),
                    Fixture.scheduled_start_at < fixture.scheduled_start_at,
                    (Fixture.home_club_id.in_((home.id, away.id)))
                    | (Fixture.away_club_id.in_((home.id, away.id))),
                )
                .limit(1)
            )
            if earlier is not None:
                raise NotReady("An earlier tournament match has not finished")
        excluded = await exclusions(session, scope, fixture)
        suspended = frozenset(UUID(r["person_id"]) for r in excluded)
        home_snapshot = await team_snapshot(
            session, home, fixture.scheduled_start_at, suspended=suspended, form_at=at
        )
        away_snapshot = await team_snapshot(
            session, away, fixture.scheduled_start_at, suspended=suspended, form_at=at
        )
        energies = {}
        for person in (*home_snapshot.players, *away_snapshot.players):
            energies[person.id] = (await recover_player(session, person.id, at)).energy_bps
        state = start_match(
            uuid5(fixture.id, "match:v1"),
            seed,
            home_snapshot,
            away_snapshot,
            initial_energy=energies,
            policy=EnginePolicy(version="match-policy-v8"),
            engine_version=FORM_ONLY_ENGINE_VERSION,
            crowd=HomeCrowd(demand.capacity, demand.fans),
            knockout=context,
        )
        match = Match(
            id=state.match_id,
            fixture_id=fixture.id,
            seed=seed,
            engine_version=state.engine_version,
            state_version=int(state.engine_version.removeprefix("regulation-v")),
            state=state_document(state),
            starting_snapshot=state_document(state),
            completed_tick=0,
            phase=state.phase.value,
            home_goals=0,
            away_goals=0,
            next_due_at=fixture.scheduled_start_at,
            paused_at=None,
            paused_seconds=0,
        )
        session.add(match)
        await session.flush()
        if scope is not None:
            from goaletto.infrastructure.models import MatchDiscipline

            session.add(
                MatchDiscipline(match_id=match.id, scope=scope, excluded=excluded, locked_at=at)
            )
            await session.flush()
        from goaletto.application.match_development import pin_development

        await pin_development(session, fixture, state, at)
        await lock_energy(session, match, state, at)
        if fixture.neutral:
            from goaletto.infrastructure.models import NeutralFinalBooking

            booking = await session.get(NeutralFinalBooking, fixture.id)
            assert booking is not None
            await pin_crowd(session, match, demand, at, neutral_basis=booking.basis)
        else:
            await pin_crowd(session, match, demand, at)
        await stage_receipt(session, match, "locked", at, fixture.lineup_lock_at)
        await lifecycle_event(session, fixture, "match_lineup_locked", "locked", at)
        return None
    if match is None:
        raise NotReady("Lineup lock has not committed")
    if job.kind == "match.start":
        if at < fixture.scheduled_start_at:
            raise NotReady("Kickoff is not due")
        if fixture.actual_start_at is not None:
            return None
        fixture.actual_start_at = at
        fixture.status = "live"
        match.next_due_at = at + timedelta(seconds=60)
        # A delayed first whistle shifts future deadlines once. Subsequent long
        # interruptions restore a decision window through a recovery receipt.
        delta = at - fixture.scheduled_start_at
        if delta != timedelta(0):
            await session.execute(
                update(Job)
                .where(
                    Job.payload["fixture_id"].astext == str(fixture.id),
                    Job.status.in_(("pending", "held")),
                    Job.kind.in_(
                        (
                            "match.tick",
                            "match.second_half_start",
                            "match.finalize_result",
                            "match.extra_start",
                            "match.extra_second_half_start",
                            "match.penalty",
                        )
                    ),
                )
                .values(due_at=Job.due_at + delta)
            )
        await stage_receipt(session, match, "started", at, fixture.scheduled_start_at)
        await lifecycle_event(session, fixture, "fixture_started", "started", at)
        await queue_notification(
            session, fixture, "match_board", "started", at, restore_state(match.state)
        )
        return None
    if fixture.actual_start_at is None:
        raise NotReady("Kickoff has not committed")
    state = restore_state(match.state)
    if (
        state.match_id != match.id
        or state.seed != match.seed
        or state.engine_version != match.engine_version
        or state.tick != match.completed_tick
        or state.phase.value != match.phase
        or match.state_version != int(state.engine_version.removeprefix("regulation-v"))
        or state.home.stats.goals != match.home_goals
        or state.away.stats.goals != match.away_goals
        or state.home.snapshot.club_id != fixture.home_club_id
        or state.away.snapshot.club_id != fixture.away_club_id
    ):
        raise ValueError("Checkpoint and persistence projection disagree")
    halftime = job.kind in {
        "match.second_half_start",
        "match.extra_start",
        "match.extra_second_half_start",
    }
    penalty = job.kind == "match.penalty"
    if halftime:
        if job.kind != "match.second_half_start":
            expected_phase = (
                MatchPhase.EXTRA_BREAK
                if job.kind == "match.extra_start"
                else MatchPhase.EXTRA_HALF_TIME
            )
            target = 31 if job.kind == "match.extra_start" else 36
            if state.tick >= target or state.phase in {MatchPhase.PENALTIES, MatchPhase.FULL_TIME}:
                return None
            if state.phase != expected_phase:
                raise NotReady("Prior extra-time segment has not committed")
        else:
            target = 16
            if state.tick > 15 or state.phase in {MatchPhase.SECOND_HALF, MatchPhase.FULL_TIME}:
                return None
            if state.phase != MatchPhase.HALF_TIME:
                raise NotReady("First half has not committed")
        due = due_for(match, fixture)
    elif penalty:
        if state.knockout is None or state.knockout.shootout is None or tick is None:
            raise NotReady("Shootout has not started")
        if tick <= len(state.knockout.shootout.kicks):
            return None
        if tick != len(state.knockout.shootout.kicks) + 1:
            raise NotReady("Prior penalty has not committed")
        due, target = due_for(match, fixture), tick
    else:
        if tick is None:
            raise ValueError("Tick is missing")
        if tick <= state.tick:
            return None
        if tick != state.tick + 1 or state.phase in BREAK_PHASES | {
            MatchPhase.HALF_TIME,
            MatchPhase.PENALTIES,
        }:
            raise NotReady("Prior transition has not committed")
        due = due_for(match, fixture, tick)
        target = tick
    if at < due:
        raise NotReady("Absolute deadline is not due")
    # Fifteen seconds of lag is beyond ordinary scheduling jitter.
    # Rebase once under the same locks; leave the pending job for its new deadline.
    if (at - due).total_seconds() >= 15:
        await restore_decision_window(session, fixture, match, state, due, at, "worker_lag")
        return "rescheduled"
    if penalty:
        penalty_result = advance_penalty(state, target)
        new, events = penalty_result.state, penalty_result.events
    else:
        new, events = await apply_commands(
            session, match, fixture, state, target, at, halftime=halftime
        )
    match.state = state_document(new)
    match.completed_tick = new.tick
    match.phase = new.phase.value
    match.home_goals = new.home.stats.goals
    match.away_goals = new.away.stats.goals
    match.next_due_at = next_due(new, match, fixture)
    if new.knockout:
        await schedule_knockout_work(session, fixture, match, new)
    if halftime:
        stage = "second_half" if target == 16 else "extra_first" if target == 31 else "extra_second"
        await stage_receipt(session, match, stage, at, due, events)
        if target == 16:
            await lifecycle_event(session, fixture, "match_second_half_started", "second_half", at)
    elif penalty:
        await stage_receipt(session, match, f"penalty:{target}", at, due, events)
    else:
        await lifecycle_event(session, fixture, "match_tick_committed", f"tick:{target}", at)
    if new.phase in {MatchPhase.FULL_TIME, MatchPhase.ABANDONED}:
        abandoned = new.phase == MatchPhase.ABANDONED
        fixture.status = "abandoned" if abandoned else "finished"
        fixture.actual_finish_at = at
        stage = "abandoned" if abandoned else "finished"
        await stage_receipt(session, match, stage, at, due)
        await lifecycle_event(session, fixture, f"fixture_{stage}", stage, at)
        if abandoned:
            from goaletto.application.discipline import apply_discipline
            from goaletto.application.injuries import apply_match_injuries
            from goaletto.application.recovery import finish_energy

            await finish_energy(session, new, at)
            await apply_match_injuries(session, new, at)
            await apply_discipline(session, new, at)
        if not abandoned and await finalize_result(session, fixture.id, at):
            await lifecycle_event(session, fixture, "result_finalized", "result", at)
        await queue_notification(session, fixture, "match_result", stage, at, new)
        await session.execute(
            update(AcceptedCommand)
            .where(AcceptedCommand.match_id == match.id, AcceptedCommand.status == "accepted")
            .values(status="cancelled")
        )
        # Abandoned fixtures never receive automatic standings/awarded scores.
        await session.execute(
            update(Job)
            .where(
                Job.payload["fixture_id"].astext == str(fixture.id),
                Job.status == "pending",
                Job.kind.in_(
                    (
                        "match.tick",
                        "match.second_half_start",
                        "match.extra_start",
                        "match.extra_second_half_start",
                        "match.penalty",
                    )
                ),
            )
            .values(status="done")
        )
    else:
        await queue_notification(
            session,
            fixture,
            "match_board",
            f"penalty:{target}" if penalty else f"tick:{new.tick}:{new.phase.value}",
            at,
            new,
        )
    return None


async def restore_decision_window(
    session: AsyncSession,
    fixture: Fixture,
    match: Match,
    state: MatchState,
    previous_due: datetime,
    at: datetime,
    reason: str,
) -> None:
    """Caller holds allocation/fixture/match locks. No engine or command mutation."""
    seconds = 180 if state.phase == MatchPhase.HALF_TIME else 60
    delay = math.ceil((at + timedelta(seconds=seconds) - previous_due).total_seconds())
    # Invalid imported/legacy rows accepted after their original deadline do not
    # gain validity merely because the clock is restored. Valid commands survive.
    expired_rows = (
        await session.scalars(
            select(AcceptedCommand)
            .where(
                AcceptedCommand.match_id == match.id,
                AcceptedCommand.status == "accepted",
                AcceptedCommand.target_tick_number <= state.tick + 1,
                AcceptedCommand.accepted_at >= previous_due,
            )
            .with_for_update()
        )
    ).all()
    for command in expired_rows:
        if (
            command.target_tick_number is not None
            and command.target_tick_number <= state.tick + 1
            and command.accepted_at >= previous_due
        ):
            command.status = "cancelled"
    match.paused_seconds += delay
    match.next_due_at = next_due(state, match, fixture)
    assert match.next_due_at is not None and match.next_due_at >= at + timedelta(seconds=seconds)
    recovery_id = uuid5(match.id, f"recovery:v1:{previous_due.isoformat()}")
    session.add(
        MatchRecovery(
            id=recovery_id,
            match_id=match.id,
            checkpoint_tick=state.tick,
            state_hash=state_hash(state),
            previous_due_at=previous_due,
            next_due_at=match.next_due_at,
            recovered_at=at,
            reason=reason,
        )
    )
    await session.execute(
        update(Job)
        .where(
            Job.payload["fixture_id"].astext == str(fixture.id),
            Job.status.in_(("pending", "held")),
            Job.kind.in_(
                (
                    "match.tick",
                    "match.second_half_start",
                    "match.finalize_result",
                    "match.extra_start",
                    "match.extra_second_half_start",
                    "match.penalty",
                )
            ),
            Job.due_at >= previous_due,
        )
        .values(due_at=Job.due_at + timedelta(seconds=delay))
    )
    await queue_notification(
        session,
        fixture,
        "match_recovery",
        f"recovery:{recovery_id}",
        at,
        state,
        resume_deadline=match.next_due_at,
    )


async def pause_match(session: AsyncSession, fixture_id: UUID, at: datetime) -> None:
    require_utc(at)
    if not session.in_transaction():
        raise ValueError("Pause requires a transaction")
    from goaletto.application.game_actions import allocation_lock

    await allocation_lock(session)
    fixture = await session.scalar(
        select(Fixture).where(Fixture.id == fixture_id).with_for_update(key_share=True)
    )
    match = await session.scalar(
        select(Match).where(Match.fixture_id == fixture_id).with_for_update()
    )
    if fixture is None or match is None or fixture.status not in {"live", "paused"}:
        raise ValueError("Only live matches can pause")
    if fixture.status == "paused":
        return
    if fixture.actual_start_at is None or at < fixture.actual_start_at:
        raise ValueError("Pause precedes kickoff")
    fixture.status = "paused"
    match.phase = "paused"
    match.paused_at = at


async def resume_match(session: AsyncSession, fixture_id: UUID, at: datetime) -> None:
    require_utc(at)
    if not session.in_transaction():
        raise ValueError("Resume requires a transaction")
    from goaletto.application.game_actions import allocation_lock

    await allocation_lock(session)
    fixture = await session.scalar(
        select(Fixture).where(Fixture.id == fixture_id).with_for_update(key_share=True)
    )
    match = await session.scalar(
        select(Match).where(Match.fixture_id == fixture_id).with_for_update()
    )
    if fixture is None or match is None or fixture.status not in {"paused", "live"}:
        raise ValueError("Only paused matches can resume")
    if fixture.status == "live":
        return
    if match.paused_at is None or at < match.paused_at:
        raise ValueError("Invalid pause interval")
    state = restore_state(match.state)
    previous_due = next_due(state, match, fixture)
    if previous_due is None:
        raise ValueError("Paused match has no decision window")
    if previous_due <= match.paused_at:
        await restore_decision_window(
            session, fixture, match, state, previous_due, at, "late_resume"
        )
        match.paused_at = None
        match.phase = state.phase.value
        fixture.status = "live"
        return
    duration = math.ceil((at - match.paused_at).total_seconds())
    match.paused_seconds += duration
    match.paused_at = None
    state = restore_state(match.state)
    match.phase = state.phase.value
    fixture.status = "live"
    match.next_due_at = next_due(state, match, fixture)
    await session.execute(
        update(Job)
        .where(
            Job.payload["fixture_id"].astext == str(fixture.id),
            Job.status.in_(("pending", "held")),
            Job.due_at >= previous_due,
            Job.kind.in_(
                (
                    "match.tick",
                    "match.second_half_start",
                    "match.finalize_result",
                    "match.extra_start",
                    "match.extra_second_half_start",
                    "match.penalty",
                )
            ),
        )
        .values(due_at=Job.due_at + timedelta(seconds=duration))
    )


async def postpone_fixture(
    session: AsyncSession,
    fixture_id: UUID,
    kickoff: datetime,
    at: datetime,
) -> None:
    """Explicit operator use case before lineup lock; never silently resets a match."""
    require_utc(at)
    require_utc(kickoff)
    if not session.in_transaction():
        raise ValueError("Postponement requires a transaction")
    fixture = await session.scalar(
        select(Fixture).where(Fixture.id == fixture_id).with_for_update(key_share=True)
    )
    match = await session.scalar(select(Match).where(Match.fixture_id == fixture_id))
    if fixture is None or fixture.status != "scheduled" or match is not None:
        raise ValueError("Only an unlocked scheduled fixture can be postponed")
    if at >= fixture.lineup_lock_at or kickoff <= at + timedelta(minutes=5):
        raise ValueError("Postponement needs an open preparation window")
    delta = kickoff - fixture.scheduled_start_at
    fixture.scheduled_start_at = kickoff
    fixture.lineup_lock_at = kickoff - timedelta(minutes=1)
    await session.execute(
        update(Job)
        .where(
            Job.payload["fixture_id"].astext == str(fixture.id),
            Job.kind.in_(RUNTIME_KINDS | {"season.rollover"}),
            Job.status.in_(("held", "pending")),
        )
        .values(due_at=Job.due_at + delta)
    )
