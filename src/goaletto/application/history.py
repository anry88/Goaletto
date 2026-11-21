"""Read-only match history, reports and season archives. No recalculation.

All numbers come from persisted rows: MatchResult receipts, MatchTick event
documents, match checkpoints (frozen names/ages/forms in starting snapshots)
and historical SeasonClub memberships with stored Standing counters. Points
are never recomputed or awarded here; league ordering reuses the pure
``rank_league`` over stored counters, exactly like the result finalizer.
V5 awards and assists come only from the frozen terminal state/events.

Pagination is bounded: club results use COUNT plus LIMIT/OFFSET with stable
ordering (scheduled_start_at DESC, fixture id), matches/receipts/ticks load
in batches only for the requested page. Player history filters participation
by the stored starting snapshot of the currently owned club (opponent
snapshots never count); minutes come from stored terminal conditions (bench
presence alone means 0 minutes). Season lists load with IN queries, no N+1.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface
from goaletto.application.game_actions import (
    Actor,
    PermissionDenied,
    owned_club,
    user_effect,
)
from goaletto.application.progress_reports import match_report_rows
from goaletto.application.results import standing_snapshot
from goaletto.domain.match_codec import restore_state
from goaletto.domain.match_types import POSITIONAL_ENGINES, MatchState
from goaletto.domain.standings import LeagueStanding, rank_league
from goaletto.infrastructure.models import (
    Club,
    Fixture,
    League,
    Match,
    MatchDevelopment,
    MatchResult,
    MatchStage,
    MatchTick,
    Person,
    Player,
    Season,
    SeasonClub,
    Standing,
)

HISTORY_PAGE_SIZE = 5


@dataclass(frozen=True)
class ClubResultEntry:
    fixture_id: UUID
    season_id: UUID
    round_number: int
    scheduled_start_at: datetime
    home_club_id: UUID
    away_club_id: UUID
    home_name: str
    away_name: str
    home_goals: int
    away_goals: int
    outcome: str  # win/draw/loss from the owned club's perspective
    competition: str = "league"
    penalties: tuple[int, int] | None = None


@dataclass(frozen=True)
class ClubResultsPage:
    club_id: UUID
    club_name: str
    entries: tuple[ClubResultEntry, ...]
    page: int
    total_pages: int
    total: int


@dataclass(frozen=True)
class TeamReportStats:
    goals: int
    shots: int
    on_target: int
    saves: int
    corners: int
    fouls: int
    possession_units: int


@dataclass(frozen=True)
class ReportEvent:
    kind: str
    tick: int
    minute: int
    side: str | None  # home/away when the stored document says so
    player_name: str | None  # frozen snapshot name at match time
    other_name: str | None
    detail: str | None
    assist_name: str | None = None


@dataclass(frozen=True)
class ReportLineupPlayer:
    person_id: UUID
    name: str  # frozen at match time
    age: int  # frozen at match time
    form: int  # frozen at match time
    position: str  # frozen starting slot
    started: bool
    minutes: int  # stored terminal condition
    goals: int  # counted from stored goal events
    yellows: int  # stored terminal condition
    sent_off: bool  # stored terminal condition
    injured: bool  # stored terminal condition
    energy_bps: int | None = None  # stored terminal condition, distinct from form
    appeared: bool = False  # includes a player entering immediately before final whistle
    growth_bps: int | None = None
    experience_gain: int | None = None
    form_before: int | None = None
    form_after: int | None = None
    skill_gains: tuple[tuple[str, int], ...] = ()
    country_code: str | None = None
    position_minutes: tuple[tuple[str, int], ...] = ()
    position_experience: tuple[tuple[str, int, int], ...] = ()
    skill_progress: tuple[tuple[str, int, int, int | None], ...] = ()
    growth_limited: bool = False
    assists: int = 0
    saves: int = 0
    player_of_match: bool = False
    global_experience: tuple[int | None, int | None, int] | None = None
    experience_percent: bool = False


@dataclass(frozen=True)
class MatchReport:
    fixture_id: UUID
    season_id: UUID
    league_id: UUID | None
    round_number: int
    scheduled_start_at: datetime
    actual_start_at: datetime | None
    actual_finish_at: datetime | None
    home_club_id: UUID
    away_club_id: UUID
    home_name: str  # frozen kickoff snapshot
    away_name: str  # frozen kickoff snapshot
    home_goals: int  # receipt, authoritative for the score
    away_goals: int  # receipt, authoritative for the score
    home_stats: TeamReportStats
    away_stats: TeamReportStats
    possession_home_pct: int  # same presentation math as result_view
    events: tuple[ReportEvent, ...]
    home_lineup: tuple[ReportLineupPlayer, ...]
    away_lineup: tuple[ReportLineupPlayer, ...]
    state_hash: str
    recorded_at: datetime
    competition: str = "league"
    penalties: tuple[int, int] | None = None
    aggregate: tuple[int, int] | None = None
    neutral_venue: tuple[str, int, int, int, int] | None = None
    discipline: tuple[tuple[str, UUID, int, bool, int], ...] = ()
    owned_club_id: UUID | None = None
    player_of_match_name: str | None = None
    supporters_gain: int | None = None


@dataclass(frozen=True)
class PlayerMatchEntry:
    fixture_id: UUID
    round_number: int
    scheduled_start_at: datetime
    home_name: str
    away_name: str
    home_goals: int
    away_goals: int
    side: str  # home/away: the player's frozen side
    minutes: int
    goals: int
    yellows: int
    sent_off: bool
    competition: str = "league"


@dataclass(frozen=True)
class PlayerHistoryPage:
    person_id: UUID
    person_name: str  # latest frozen snapshot name
    entries: tuple[PlayerMatchEntry, ...]
    page: int
    total_pages: int
    total: int
    country_code: str | None = None


@dataclass(frozen=True)
class SeasonSummary:
    season_id: UUID
    number: int
    tier: int


@dataclass(frozen=True)
class SeasonHistoryPage:
    entries: tuple[SeasonSummary, ...]
    page: int
    total_pages: int
    total: int


@dataclass(frozen=True)
class SeasonArchive:
    season_id: UUID
    number: int
    tier: int
    league_id: UUID
    table: tuple[LeagueStanding, ...]  # ordered stored counters, no re-awards
    club_position: int
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    points: int

    vip_clubs: frozenset[UUID] = frozenset()


def _page_count(total: int) -> int:
    return max(1, (total + HISTORY_PAGE_SIZE - 1) // HISTORY_PAGE_SIZE)


def _validate_page(page: int) -> None:
    if type(page) is not int or page < 1 or page > 10000:
        raise ValueError("Invalid history page")


def _decode_event(raw: object, names: dict[UUID, str]) -> ReportEvent | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    tick = raw.get("tick")
    minute = raw.get("minute")
    if not isinstance(kind, str) or not kind:
        return None
    if type(tick) is not int or type(minute) is not int:
        return None
    side = raw.get("side")
    side_value = side if side in ("home", "away") else None

    def _name(key: str) -> str | None:
        value = raw.get(key)
        if not isinstance(value, str):
            return None
        try:
            return names.get(UUID(value))
        except ValueError:
            return None

    detail = raw.get("detail")
    return ReportEvent(
        kind,
        tick,
        minute,
        side_value,
        _name("player_id"),
        _name("other_player_id"),
        detail if isinstance(detail, str) else None,
        _name("assist_player_id"),
    )


def _fixture_filters(club_id: UUID, season_id: UUID | None = None) -> list[Any]:
    filters: list[Any] = [
        or_(Fixture.home_club_id == club_id, Fixture.away_club_id == club_id),
        Fixture.status == "finished",
    ]
    if season_id is not None:
        from goaletto.application.champions import host_seasons_for

        filters.append(
            or_(
                Fixture.season_id == season_id,
                (Fixture.competition == "champions")
                & Fixture.season_id.in_(host_seasons_for(season_id)),
            )
        )
    return filters


def _player_snapshot_predicate(club_id: UUID, person_id: UUID) -> Any:
    """SQL-side participation filter for the owned club snapshot side.

    Uses JSONB containment so COUNT and page queries only touch the requested
    page snapshots. Python re-checks decoded snapshots afterwards, so test
    doubles or drift cannot leak opponent appearances.
    """
    home_doc = {
        "home": {
            "snapshot": {
                "club_id": str(club_id),
                "players": [{"id": str(person_id)}],
            }
        }
    }
    away_doc = {
        "away": {
            "snapshot": {
                "club_id": str(club_id),
                "players": [{"id": str(person_id)}],
            }
        }
    }
    return or_(
        Match.starting_snapshot.contains(home_doc),
        Match.starting_snapshot.contains(away_doc),
    )


def _merge_event_docs(
    tick_rows: list[MatchTick],
    stage_rows: list[MatchStage],
) -> list[dict[str, object]]:
    """Merge tick plus stage docs preserving stored order, halftime included."""
    chunks: list[tuple[int, int, list[dict[str, object]]]] = [
        (row.tick_number, 0, list(row.events or ())) for row in tick_rows
    ]
    for row in stage_rows:
        for raw in row.events or ():
            if isinstance(raw, dict) and type(raw.get("tick")) is int:
                tick_value = raw["tick"]
                assert isinstance(tick_value, int)
                chunks.append((tick_value, 1, [raw]))
    seen: set[str] = set()
    result: list[dict[str, object]] = []
    for _, _, docs in sorted(chunks, key=lambda item: (item[0], item[1])):
        for raw in docs or ():
            if not isinstance(raw, dict):
                continue
            identity = raw.get("id")
            if isinstance(identity, str):
                if identity in seen:
                    continue
                seen.add(identity)
            result.append(raw)
    return result


def _merge_grouped_events(
    ticks_by_match: dict[UUID, list[MatchTick]],
    stages_by_match: dict[UUID, list[MatchStage]],
    match_ids: list[UUID],
) -> dict[UUID, list[dict[str, object]]]:
    out: dict[UUID, list[dict[str, object]]] = {}
    for match_id in match_ids:
        out[match_id] = _merge_event_docs(
            ticks_by_match.get(match_id, []),
            stages_by_match.get(match_id, []),
        )
    return out


async def get_club_results(
    session: AsyncSession, actor: Actor, page: int, *, season_id: UUID | None = None
) -> ClubResultsPage:
    _validate_page(page)
    if season_id is not None and not isinstance(season_id, UUID):
        raise ValueError("Invalid season identity")
    if not session.in_transaction():
        raise ValueError("History views need a transaction")
    ownership, club = await owned_club(session, actor)
    if season_id is not None:
        membership = await session.scalar(
            select(SeasonClub).where(
                SeasonClub.season_id == season_id, SeasonClub.club_id == club.id
            )
        )
        if membership is None:
            raise PermissionDenied("Season is not available")
    filters = _fixture_filters(club.id, season_id)
    total_value = await session.scalar(select(func.count()).select_from(Fixture).where(*filters))
    total = int(total_value or 0)
    total_pages = _page_count(total)
    if page > total_pages:
        raise ValueError("Invalid history page")
    offset = (page - 1) * HISTORY_PAGE_SIZE
    fixtures = (
        await session.scalars(
            select(Fixture)
            .where(*filters)
            .order_by(Fixture.scheduled_start_at.desc(), Fixture.id)
            .limit(HISTORY_PAGE_SIZE)
            .offset(offset)
        )
    ).all()
    chosen_ids = [f.id for f in fixtures]
    matches_by_fixture: dict[UUID, Match] = {}
    if chosen_ids:
        match_rows = (
            await session.scalars(select(Match).where(Match.fixture_id.in_(chosen_ids)))
        ).all()
        matches_by_fixture = {m.fixture_id: m for m in match_rows}
    receipts_by_fixture: dict[UUID, MatchResult] = {}
    if chosen_ids:
        receipt_rows = (
            await session.scalars(select(MatchResult).where(MatchResult.fixture_id.in_(chosen_ids)))
        ).all()
        receipts_by_fixture = {r.fixture_id: r for r in receipt_rows}
    entries: list[ClubResultEntry] = []
    for fixture in fixtures:
        match = matches_by_fixture.get(fixture.id)
        if match is None:
            raise ValueError("Fixture has no match checkpoint")
        receipt = receipts_by_fixture.get(fixture.id)
        if receipt is None:
            raise ValueError("Finished fixture has no immutable result receipt")
        initial = restore_state(match.starting_snapshot)
        mine_home = fixture.home_club_id == club.id
        scored = receipt.home_goals if mine_home else receipt.away_goals
        conceded = receipt.away_goals if mine_home else receipt.home_goals
        outcome = "win" if scored > conceded else "draw" if scored == conceded else "loss"
        penalties = None
        if (fixture.competition or "league") in ("cup", "champions"):
            terminal = restore_state(match.state)
            if terminal.knockout and terminal.knockout.shootout:
                from goaletto.domain.knockout import decide_shootout

                shootout = decide_shootout(terminal.knockout.shootout)
                penalties = (
                    (shootout.score_a, shootout.score_b)
                    if terminal.home.snapshot.club_id == terminal.knockout.tie.club_a_id
                    else (shootout.score_b, shootout.score_a)
                )
                outcome = "win" if shootout.winner == club.id else "loss"
        entries.append(
            ClubResultEntry(
                fixture.id,
                fixture.season_id,
                fixture.round_number,
                fixture.scheduled_start_at,
                fixture.home_club_id,
                fixture.away_club_id,
                initial.home.snapshot.name,
                initial.away.snapshot.name,
                receipt.home_goals,
                receipt.away_goals,
                outcome,
                fixture.competition or "league",
                penalties,
            )
        )
    await user_effect(
        session, actor, "club_opened", Surface.CLUB, ownership=ownership, club_id=club.id
    )
    return ClubResultsPage(club.id, club.name, tuple(entries), page, total_pages, total)


async def _club_names(session: AsyncSession, club_ids: set[UUID]) -> dict[UUID, str]:
    if not club_ids:
        return {}
    rows = (await session.scalars(select(Club).where(Club.id.in_(club_ids)))).all()
    return {row.id: row.name for row in rows}


async def stored_events(session: AsyncSession, match_id: UUID) -> list[dict[str, object]]:
    """Include halftime/stage commands, preserving stored order and event IDs."""
    grouped = await stored_events_batch(session, [match_id])
    return grouped.get(match_id, [])


async def stored_events_batch(
    session: AsyncSession, match_ids: list[UUID]
) -> dict[UUID, list[dict[str, object]]]:
    """Batch variant: two queries for any number of matches on one page."""
    if not match_ids:
        return {}
    unique_ids = list(dict.fromkeys(match_ids))
    tick_rows = (
        await session.scalars(
            select(MatchTick)
            .where(MatchTick.match_id.in_(unique_ids))
            .order_by(MatchTick.match_id, MatchTick.tick_number)
        )
    ).all()
    stage_rows = (
        await session.scalars(
            select(MatchStage)
            .where(MatchStage.match_id.in_(unique_ids))
            .order_by(MatchStage.match_id, MatchStage.occurred_at, MatchStage.stage)
        )
    ).all()
    ticks_by_match: dict[UUID, list[MatchTick]] = {mid: [] for mid in unique_ids}
    for tick_row in tick_rows:
        ticks_by_match.setdefault(tick_row.match_id, []).append(tick_row)
    stages_by_match: dict[UUID, list[MatchStage]] = {mid: [] for mid in unique_ids}
    for stage_row in stage_rows:
        stages_by_match.setdefault(stage_row.match_id, []).append(stage_row)
    return _merge_grouped_events(ticks_by_match, stages_by_match, unique_ids)


async def get_match_report(session: AsyncSession, actor: Actor, fixture_id: UUID) -> MatchReport:
    if not isinstance(fixture_id, UUID):
        raise ValueError("Invalid fixture identity")
    if not session.in_transaction():
        raise ValueError("History views need a transaction")
    ownership, club = await owned_club(session, actor)
    fixture = await session.scalar(select(Fixture).where(Fixture.id == fixture_id))
    if fixture is None or club.id not in (fixture.home_club_id, fixture.away_club_id):
        raise PermissionDenied("Match is not available")
    if fixture.status != "finished":
        raise ValueError("Only finished fixtures have reports")
    match = await session.scalar(select(Match).where(Match.fixture_id == fixture.id))
    if match is None:
        raise ValueError("Fixture has no match checkpoint")
    receipt = await session.get(MatchResult, match.id)
    if receipt is None:
        raise ValueError("Finished fixture has no immutable result receipt")
    terminal = restore_state(match.state)
    initial = restore_state(match.starting_snapshot)
    from goaletto.domain.match_performance import player_of_match

    best_player = player_of_match(terminal)
    names = {
        p.id: p.name
        for team in (initial.home.snapshot, initial.away.snapshot)
        for p in team.players
    }
    development = await session.get(MatchDevelopment, match.id)
    country_ids = set(names)
    if development and development.applied_at:
        country_ids.update(UUID(row["person_id"]) for row in development.results)
    countries = {
        key: code
        for key, code in (
            await session.execute(
                select(Person.id, Person.country_code).where(Person.id.in_(country_ids))
            )
        ).all()
    }
    raw_events = await stored_events(session, match.id)
    events: list[ReportEvent] = []
    for raw in raw_events:
        decoded = _decode_event(raw, names)
        if decoded is not None:
            events.append(decoded)
    goal_counts: dict[tuple[str, UUID], int] = {}
    for raw in raw_events:
        if raw.get("kind") != "goal":
            continue
        side = raw.get("side")
        scorer = _raw_person(raw.get("player_id"))
        if side in ("home", "away") and scorer is not None:
            key = (side, scorer)
            goal_counts[key] = goal_counts.get(key, 0) + 1

    from goaletto.infrastructure.models import DisciplineReceipt, MatchDiscipline

    discipline_header = await session.get(MatchDiscipline, match.id)
    discipline = (
        tuple(
            (r.name, r.club_id, r.awarded, r.served, r.carried_after)
            for r in await session.scalars(
                select(DisciplineReceipt)
                .where(DisciplineReceipt.match_id == match.id)
                .order_by(DisciplineReceipt.club_id, DisciplineReceipt.person_id)
            )
        )
        if discipline_header and discipline_header.applied_at
        else ()
    )
    growth_rows = (
        {
            r["person_id"]: r
            for r in await match_report_rows(
                session,
                [r for r in development.results if r["club_id"] == str(club.id)],
                development.applied_at,
            )
        }
        if development and development.applied_at
        else {}
    )

    def _lineup(side: str) -> tuple[ReportLineupPlayer, ...]:
        initial_team = initial.home if side == "home" else initial.away
        final_team = terminal.home if side == "home" else terminal.away
        conditions = {c.player_id: c for c in final_team.conditions}
        starters = {s.player_id: s.position for s in initial_team.lineup}
        rows_out: list[ReportLineupPlayer] = []
        for snapshot_player in initial_team.snapshot.players:
            condition = conditions.get(snapshot_player.id)
            rows_out.append(
                ReportLineupPlayer(
                    snapshot_player.id,
                    snapshot_player.name,
                    snapshot_player.age,
                    snapshot_player.form,
                    starters.get(snapshot_player.id, snapshot_player.position),
                    snapshot_player.id in starters,
                    condition.minutes_played if condition else 0,
                    condition.goals
                    if terminal.engine_version in POSITIONAL_ENGINES and condition
                    else goal_counts.get((side, snapshot_player.id), 0),
                    condition.yellows if condition else 0,
                    condition.dismissed if condition else False,
                    condition.injured if condition else False,
                    condition.energy_bps if condition else None,
                    snapshot_player.id in starters
                    or bool(condition and condition.minutes_played)
                    or any(
                        raw.get("kind") in {"substitution", "automatic_substitution"}
                        and raw.get("side") == side
                        and _raw_person(raw.get("other_player_id")) == snapshot_player.id
                        for raw in raw_events
                    ),
                    growth_rows.get(str(snapshot_player.id), {}).get("gain_bps"),
                    growth_rows.get(str(snapshot_player.id), {}).get("experience_gain"),
                    growth_rows.get(str(snapshot_player.id), {}).get("form_before"),
                    growth_rows.get(str(snapshot_player.id), {}).get("form_after"),
                    tuple(
                        (key, value)
                        for key, value in growth_rows.get(str(snapshot_player.id), {})
                        .get("granted", {})
                        .items()
                        if value
                    ),
                    country_code=countries.get(snapshot_player.id),
                    position_minutes=tuple(
                        (r["position"], r["minutes"])
                        for r in growth_rows.get(str(snapshot_player.id), {}).get(
                            "position_terms", []
                        )
                    ),
                    position_experience=tuple(
                        (position, values["before"], values["after"])
                        for position, values in growth_rows.get(str(snapshot_player.id), {})
                        .get("position_experience", {})
                        .items()
                    ),
                    skill_progress=tuple(
                        (skill, values["before_value"], values["after_value"], values["after"])
                        for skill, values in growth_rows.get(str(snapshot_player.id), {})
                        .get("skill_progress", {})
                        .items()
                    ),
                    growth_limited=growth_rows.get(str(snapshot_player.id), {}).get(
                        "growth_limited", False
                    ),
                    assists=condition.assists if condition else 0,
                    saves=condition.saves if condition else 0,
                    player_of_match=snapshot_player.id == best_player,
                    experience_percent=growth_rows.get(str(snapshot_player.id), {}).get(
                        "experience_policy"
                    )
                    == "position-experience-v2",
                    global_experience=(
                        (
                            growth_rows[str(snapshot_player.id)]["experience_progress"]["before"],
                            growth_rows[str(snapshot_player.id)]["experience_progress"]["after"],
                            growth_rows[str(snapshot_player.id)]["experience_progress"]["gain"],
                        )
                        if growth_rows.get(str(snapshot_player.id), {}).get("experience_progress")
                        else None
                    ),
                )
            )
        rows_out.sort(key=lambda r: (not r.started, not r.appeared, r.name, str(r.person_id)))
        visible = {str(member.person_id) for member in rows_out}
        for person_id, row in sorted(growth_rows.items()):
            if (
                person_id in visible
                or row.get("club_id") != str(initial_team.snapshot.club_id)
                or row.get("experience_policy") != "position-experience-v2"
                or not row.get("name")
            ):
                continue
            rows_out.append(
                ReportLineupPlayer(
                    UUID(person_id),
                    row["name"],
                    row["age"],
                    row["form"],
                    row["position"],
                    False,
                    0,
                    0,
                    0,
                    False,
                    False,
                    growth_bps=0,
                    position_experience=tuple(
                        (position, values["before"], values["after"])
                        for position, values in row["position_experience"].items()
                    ),
                    experience_percent=True,
                    country_code=countries.get(UUID(person_id)),
                )
            )
        return tuple(rows_out)

    def _stats(side: str) -> TeamReportStats:
        stats = terminal.home.stats if side == "home" else terminal.away.stats
        return TeamReportStats(
            stats.goals,
            stats.shots,
            stats.on_target,
            stats.saves,
            stats.corners,
            stats.fouls,
            stats.possession_units,
        )

    total_units = terminal.home.stats.possession_units + terminal.away.stats.possession_units
    share = round(terminal.home.stats.possession_units * 100 / total_units) if total_units else 50
    await user_effect(
        session,
        actor,
        "match_report_opened",
        Surface.REPORT,
        ownership=ownership,
        club_id=club.id,
        fixture_id=fixture.id,
    )
    penalties = aggregate = None
    neutral_venue = None
    if fixture.neutral:
        from goaletto.infrastructure.models import NeutralFinalBooking

        booking = await session.get(NeutralFinalBooking, fixture.id)
        if booking is None:
            raise ValueError("Neutral final has no venue snapshot")
        b = booking.basis
        neutral_venue = (
            booking.venue_name,
            b["capacity"],
            b["crowd"],
            b["payout_a"],
            b["payout_b"],
        )
    if terminal.knockout:
        from goaletto.domain.knockout_match import decision

        outcome = decision(terminal)
        direct = terminal.home.snapshot.club_id == terminal.knockout.tie.club_a_id
        if terminal.knockout.segment != "leg1":
            aggregate = (
                (outcome.aggregate_a, outcome.aggregate_b)
                if direct
                else (outcome.aggregate_b, outcome.aggregate_a)
            )
        if terminal.knockout.shootout:
            penalties = (
                (outcome.penalty_a, outcome.penalty_b)
                if direct
                else (outcome.penalty_b, outcome.penalty_a)
            )
    from goaletto.infrastructure.models import SupporterChange

    supporters_change = await session.scalar(
        select(SupporterChange).where(
            SupporterChange.club_id == club.id, SupporterChange.match_id == match.id
        )
    )
    return MatchReport(
        fixture.id,
        fixture.season_id,
        fixture.league_id,
        fixture.round_number,
        fixture.scheduled_start_at,
        fixture.actual_start_at,
        fixture.actual_finish_at,
        fixture.home_club_id,
        fixture.away_club_id,
        terminal.home.snapshot.name,
        terminal.away.snapshot.name,
        receipt.home_goals,
        receipt.away_goals,
        _stats("home"),
        _stats("away"),
        share,
        tuple(events),
        _lineup("home"),
        _lineup("away"),
        receipt.state_hash,
        receipt.recorded_at,
        fixture.competition or "league",
        penalties,
        aggregate,
        neutral_venue,
        discipline,
        club.id,
        names.get(best_player) if best_player else None,
        supporters_change.after_count - supporters_change.before_count
        if supporters_change
        else None,
    )


def _player_side_for_club(
    initial: MatchState, club_id: UUID, person_id: UUID
) -> tuple[str | None, str]:
    """Return (side, frozen name) for the owned club snapshot side only."""
    for candidate in ("home", "away"):
        team = initial.home if candidate == "home" else initial.away
        if team.snapshot.club_id != club_id:
            continue
        for snapshot_player in team.snapshot.players:
            if snapshot_player.id == person_id:
                return candidate, snapshot_player.name
    return None, ""


async def get_player_matches(
    session: AsyncSession, actor: Actor, person_id: UUID, page: int
) -> PlayerHistoryPage:
    if not isinstance(person_id, UUID):
        raise ValueError("Invalid player identity")
    _validate_page(page)
    if not session.in_transaction():
        raise ValueError("History views need a transaction")
    ownership, club = await owned_club(session, actor)
    live = await session.scalar(
        select(Player).where(Player.person_id == person_id, Player.club_id == club.id)
    )
    fixture_filters = _fixture_filters(club.id)
    player_predicate = _player_snapshot_predicate(club.id, person_id)
    total_value = await session.scalar(
        select(func.count())
        .select_from(Fixture)
        .join(Match, Match.fixture_id == Fixture.id)
        .where(*fixture_filters, player_predicate)
    )
    total = int(total_value or 0)
    if total == 0:
        if live is None:
            raise PermissionDenied("Player has no history with the current club")
        if page != 1:
            raise ValueError("Invalid history page")
        person_row = await session.get(Person, person_id)
        fallback_name = person_row.name if person_row is not None else "Игрок"
        await user_effect(
            session, actor, "lineup_opened", Surface.SQUAD, ownership=ownership, club_id=club.id
        )
        return PlayerHistoryPage(
            person_id,
            fallback_name,
            (),
            page,
            1,
            0,
            country_code=person_row.country_code if person_row else None,
        )
    total_pages = _page_count(total)
    if page > total_pages:
        raise ValueError("Invalid history page")
    offset = (page - 1) * HISTORY_PAGE_SIZE
    page_fixtures = (
        await session.scalars(
            select(Fixture)
            .join(Match, Match.fixture_id == Fixture.id)
            .where(*fixture_filters, player_predicate)
            .order_by(Fixture.scheduled_start_at.desc(), Fixture.id)
            .limit(HISTORY_PAGE_SIZE)
            .offset(offset)
        )
    ).all()
    fixture_ids = [f.id for f in page_fixtures]
    matches_by_fixture: dict[UUID, Match] = {}
    receipts_by_fixture: dict[UUID, MatchResult] = {}
    if fixture_ids:
        for match_row in (
            await session.scalars(select(Match).where(Match.fixture_id.in_(fixture_ids)))
        ).all():
            matches_by_fixture[match_row.fixture_id] = match_row
        for receipt_row in (
            await session.scalars(
                select(MatchResult).where(MatchResult.fixture_id.in_(fixture_ids))
            )
        ).all():
            receipts_by_fixture[receipt_row.fixture_id] = receipt_row
    match_ids = [matches_by_fixture[fid].id for fid in fixture_ids if fid in matches_by_fixture]
    events_by_match = await stored_events_batch(session, match_ids)
    entries: list[PlayerMatchEntry] = []
    person_name = ""
    for fixture in page_fixtures:
        match = matches_by_fixture.get(fixture.id)
        receipt = receipts_by_fixture.get(fixture.id)
        if match is None or receipt is None:
            raise ValueError("Finished fixture has no immutable match result")
        initial = restore_state(match.starting_snapshot)
        terminal = restore_state(match.state)
        side, snapshot_name = _player_side_for_club(initial, club.id, person_id)
        if side is None:
            continue
        if not person_name:
            person_name = snapshot_name
        final_team = terminal.home if side == "home" else terminal.away
        condition = next((c for c in final_team.conditions if c.player_id == person_id), None)
        raw_events = events_by_match.get(match.id, [])
        goals = sum(
            1
            for raw in raw_events
            if raw.get("kind") == "goal"
            and raw.get("side") == side
            and _raw_person(raw.get("player_id")) == person_id
        )
        entries.append(
            PlayerMatchEntry(
                fixture.id,
                fixture.round_number,
                fixture.scheduled_start_at,
                terminal.home.snapshot.name,
                terminal.away.snapshot.name,
                receipt.home_goals,
                receipt.away_goals,
                side,
                condition.minutes_played if condition else 0,
                goals,
                condition.yellows if condition else 0,
                condition.dismissed if condition else False,
                fixture.competition or "league",
            )
        )
    if not entries and not person_name and live is None:
        raise PermissionDenied("Player has no history with the current club")
    if person_name == "":
        person_row = await session.get(Person, person_id)
        person_name = person_row.name if person_row is not None else "Игрок"
    await user_effect(
        session, actor, "lineup_opened", Surface.SQUAD, ownership=ownership, club_id=club.id
    )
    country = await session.scalar(select(Person.country_code).where(Person.id == person_id))
    return PlayerHistoryPage(
        person_id, person_name, tuple(entries), page, total_pages, total, country_code=country
    )


def _raw_person(value: object) -> UUID | None:
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


async def get_club_seasons(session: AsyncSession, actor: Actor) -> tuple[SeasonSummary, ...]:
    if not session.in_transaction():
        raise ValueError("History views need a transaction")
    ownership, club = await owned_club(session, actor)
    memberships = (
        await session.scalars(select(SeasonClub).where(SeasonClub.club_id == club.id))
    ).all()
    if not memberships:
        await user_effect(
            session, actor, "club_opened", Surface.CLUB, ownership=ownership, club_id=club.id
        )
        return ()
    season_ids = {m.season_id for m in memberships}
    league_ids = {m.league_id for m in memberships}
    season_rows = (await session.scalars(select(Season).where(Season.id.in_(season_ids)))).all()
    league_rows = (await session.scalars(select(League).where(League.id.in_(league_ids)))).all()
    seasons_by_id = {s.id: s for s in season_rows}
    leagues_by_id = {row.id: row for row in league_rows}
    summaries: list[SeasonSummary] = []
    for membership in memberships:
        season = seasons_by_id.get(membership.season_id)
        league = leagues_by_id.get(membership.league_id)
        if season is None or league is None:
            raise ValueError("Season membership is missing its season or league")
        summaries.append(SeasonSummary(season.id, season.number, league.tier))
    summaries.sort(
        key=lambda summary: (seasons_by_id[summary.season_id].starts_at, summary.season_id),
        reverse=True,
    )
    await user_effect(
        session, actor, "club_opened", Surface.CLUB, ownership=ownership, club_id=club.id
    )
    return tuple(summaries)


async def get_club_seasons_page(
    session: AsyncSession, actor: Actor, page: int
) -> SeasonHistoryPage:
    """Bounded season list for Telegram; the legacy all-seasons API stays compatible."""
    _validate_page(page)
    ownership, club = await owned_club(session, actor)
    total = int(
        await session.scalar(
            select(func.count()).select_from(SeasonClub).where(SeasonClub.club_id == club.id)
        )
        or 0
    )
    pages = _page_count(total)
    if page > pages:
        raise ValueError("Invalid history page")
    rows = (
        await session.execute(
            select(Season.id, Season.number, League.tier)
            .select_from(SeasonClub)
            .join(Season, Season.id == SeasonClub.season_id)
            .join(League, League.id == SeasonClub.league_id)
            .where(SeasonClub.club_id == club.id)
            .order_by(Season.starts_at.desc(), Season.id)
            .limit(HISTORY_PAGE_SIZE)
            .offset((page - 1) * HISTORY_PAGE_SIZE)
        )
    ).all()
    entries = tuple(SeasonSummary(row[0], row[1], row[2]) for row in rows)
    await user_effect(
        session, actor, "club_opened", Surface.CLUB, ownership=ownership, club_id=club.id
    )
    return SeasonHistoryPage(entries, page, pages, total)


async def get_season_archive(session: AsyncSession, actor: Actor, season_id: UUID) -> SeasonArchive:
    if not isinstance(season_id, UUID):
        raise ValueError("Invalid season identity")
    if not session.in_transaction():
        raise ValueError("History views need a transaction")
    ownership, club = await owned_club(session, actor)
    membership = await session.scalar(
        select(SeasonClub).where(SeasonClub.season_id == season_id, SeasonClub.club_id == club.id)
    )
    if membership is None:
        raise PermissionDenied("Season is not available")
    season = await session.get(Season, season_id)
    league = await session.get(League, membership.league_id)
    if season is None or league is None:
        raise ValueError("Season membership is missing its season or league")
    league_members = (
        await session.scalars(
            select(SeasonClub).where(
                SeasonClub.season_id == season_id, SeasonClub.league_id == league.id
            )
        )
    ).all()
    member_ids = [m.club_id for m in league_members]
    rows = (
        await session.scalars(
            select(Standing).where(
                Standing.season_id == season_id, Standing.club_id.in_(member_ids)
            )
        )
    ).all()
    names = await _club_names(session, set(member_ids))
    member_by_id = {m.club_id: m for m in league_members}
    table = rank_league(
        tuple(
            standing_snapshot(row, member_by_id[row.club_id], names.get(row.club_id, "?"))
            for row in rows
        )
    )
    position = next((i for i, row in enumerate(table, start=1) if row.club_id == club.id), None)
    own = next((row for row in rows if row.club_id == club.id), None)
    if position is None or own is None:
        raise ValueError("Season table is missing the owned club")
    await user_effect(
        session, actor, "club_opened", Surface.CLUB, ownership=ownership, club_id=club.id
    )
    from goaletto.application.cosmetics import vip_clubs

    return SeasonArchive(
        season.id,
        season.number,
        league.tier,
        league.id,
        table,
        position,
        own.played,
        own.won,
        own.drawn,
        own.lost,
        own.goals_for,
        own.goals_against,
        own.points,
        await vip_clubs(session, set(member_ids), actor.at),
    )
