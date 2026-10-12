"""Read-only match history, reports and season archives. No recalculation.

All numbers come from persisted rows: MatchResult receipts, MatchTick event
documents, match checkpoints (frozen names/ages/forms in starting snapshots)
and historical SeasonClub memberships with stored Standing counters. Points
are never recomputed or awarded here; league ordering reuses the pure
``rank_league`` over stored counters, exactly like the result finalizer.
No MVP or other derived ratings: new indicators need an approved formula.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface
from goaletto.application.game_actions import (
    Actor,
    PermissionDenied,
    owned_club,
    user_effect,
)
from goaletto.application.results import standing_snapshot
from goaletto.domain.match_codec import restore_state
from goaletto.domain.standings import LeagueStanding, rank_league
from goaletto.infrastructure.models import (
    Club,
    Fixture,
    League,
    Match,
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


@dataclass(frozen=True)
class MatchReport:
    fixture_id: UUID
    season_id: UUID
    league_id: UUID
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


@dataclass(frozen=True)
class PlayerHistoryPage:
    person_id: UUID
    person_name: str  # latest frozen snapshot name
    entries: tuple[PlayerMatchEntry, ...]
    page: int
    total_pages: int
    total: int


@dataclass(frozen=True)
class SeasonSummary:
    season_id: UUID
    number: int
    tier: int


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


def _page_count(total: int) -> int:
    return max(1, (total + HISTORY_PAGE_SIZE - 1) // HISTORY_PAGE_SIZE)


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
    )


async def get_club_results(
    session: AsyncSession, actor: Actor, page: int, *, season_id: UUID | None = None
) -> ClubResultsPage:
    if type(page) is not int or page < 1 or page > 10000:
        raise ValueError("Invalid history page")
    if season_id is not None and not isinstance(season_id, UUID):
        raise ValueError("Invalid season identity")
    if not session.in_transaction():
        raise ValueError("History views need a transaction")
    ownership, club = await owned_club(session, actor)
    filters = [
        or_(Fixture.home_club_id == club.id, Fixture.away_club_id == club.id),
        Fixture.status == "finished",
    ]
    if season_id is not None:
        filters.append(Fixture.season_id == season_id)
    fixtures = (
        await session.scalars(
            select(Fixture).where(*filters).order_by(Fixture.scheduled_start_at.desc(), Fixture.id)
        )
    ).all()
    total = len(fixtures)
    total_pages = _page_count(total)
    if page > total_pages:
        raise ValueError("Invalid history page")
    chosen = fixtures[(page - 1) * HISTORY_PAGE_SIZE : page * HISTORY_PAGE_SIZE]
    chosen_ids = [f.id for f in chosen]
    matches = (
        (await session.scalars(select(Match).where(Match.fixture_id.in_(chosen_ids)))).all()
        if chosen_ids
        else []
    )
    match_by_fixture = {m.fixture_id: m for m in matches}
    receipts: dict[UUID, MatchResult] = {}
    for fixture_id in chosen_ids:
        match = match_by_fixture.get(fixture_id)
        if match is None:
            raise ValueError("Fixture has no match checkpoint")
        receipt = await session.get(MatchResult, match.id)
        if receipt is None:
            raise ValueError("Finished fixture has no immutable result receipt")
        receipts[fixture_id] = receipt
    entries: list[ClubResultEntry] = []
    for fixture in chosen:
        receipt = receipts[fixture.id]
        mine_home = fixture.home_club_id == club.id
        scored = receipt.home_goals if mine_home else receipt.away_goals
        conceded = receipt.away_goals if mine_home else receipt.home_goals
        outcome = "win" if scored > conceded else "draw" if scored == conceded else "loss"
        entries.append(
            ClubResultEntry(
                fixture.id,
                fixture.season_id,
                fixture.round_number,
                fixture.scheduled_start_at,
                fixture.home_club_id,
                fixture.away_club_id,
                restore_state(match_by_fixture[fixture.id].starting_snapshot).home.snapshot.name,
                restore_state(match_by_fixture[fixture.id].starting_snapshot).away.snapshot.name,
                receipt.home_goals,
                receipt.away_goals,
                outcome,
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
    ticks = (
        await session.scalars(
            select(MatchTick).where(MatchTick.match_id == match_id).order_by(MatchTick.tick_number)
        )
    ).all()
    stages = (
        await session.scalars(
            select(MatchStage)
            .where(MatchStage.match_id == match_id)
            .order_by(MatchStage.occurred_at, MatchStage.stage)
        )
    ).all()
    chunks = [(row.tick_number, 0, row.events) for row in ticks]
    for row in stages:
        for raw in row.events or ():
            if isinstance(raw, dict) and type(raw.get("tick")) is int:
                chunks.append((raw["tick"], 1, [raw]))
    seen = set()
    result = []
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
    names = {
        p.id: p.name
        for team in (initial.home.snapshot, initial.away.snapshot)
        for p in team.players
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
                    goal_counts.get((side, snapshot_player.id), 0),
                    condition.yellows if condition else 0,
                    condition.dismissed if condition else False,
                    condition.injured if condition else False,
                )
            )
        rows_out.sort(key=lambda r: (not r.started, r.name, str(r.person_id)))
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
    )


async def get_player_matches(
    session: AsyncSession, actor: Actor, person_id: UUID, page: int
) -> PlayerHistoryPage:
    if not isinstance(person_id, UUID):
        raise ValueError("Invalid player identity")
    if type(page) is not int or page < 1 or page > 10000:
        raise ValueError("Invalid history page")
    if not session.in_transaction():
        raise ValueError("History views need a transaction")
    ownership, club = await owned_club(session, actor)
    fixtures = (
        await session.scalars(
            select(Fixture)
            .where(
                or_(Fixture.home_club_id == club.id, Fixture.away_club_id == club.id),
                Fixture.status == "finished",
            )
            .order_by(Fixture.scheduled_start_at.desc(), Fixture.id)
        )
    ).all()
    live = await session.scalar(
        select(Player).where(Player.person_id == person_id, Player.club_id == club.id)
    )
    entries: list[PlayerMatchEntry] = []
    person_name = ""
    for fixture in fixtures:
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture.id))
        if match is None:
            continue
        receipt = await session.get(MatchResult, match.id)
        if receipt is None:
            continue
        initial = restore_state(match.starting_snapshot)
        terminal = restore_state(match.state)
        side: str | None = None
        snapshot_name = ""
        for candidate in ("home", "away"):
            team = initial.home if candidate == "home" else initial.away
            if team.snapshot.club_id != club.id:
                continue
            for snapshot_player in team.snapshot.players:
                if snapshot_player.id == person_id:
                    side = candidate
                    snapshot_name = snapshot_player.name
        if side is None:
            continue
        if not person_name:
            person_name = snapshot_name
        final_team = terminal.home if side == "home" else terminal.away
        condition = next((c for c in final_team.conditions if c.player_id == person_id), None)
        raw_events = await stored_events(session, match.id)
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
            )
        )
    if not entries and live is None:
        raise PermissionDenied("Player has no history with the current club")
    if person_name == "":
        person_row = await session.get(Person, person_id)
        person_name = person_row.name if person_row is not None else "Игрок"
    total = len(entries)
    total_pages = _page_count(total)
    if page > total_pages:
        raise ValueError("Invalid history page")
    await user_effect(
        session, actor, "lineup_opened", Surface.SQUAD, ownership=ownership, club_id=club.id
    )
    return PlayerHistoryPage(
        person_id,
        person_name,
        tuple(entries[(page - 1) * HISTORY_PAGE_SIZE : page * HISTORY_PAGE_SIZE]),
        page,
        total_pages,
        total,
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
    summaries: list[SeasonSummary] = []
    for membership in memberships:
        season = await session.get(Season, membership.season_id)
        league = await session.get(League, membership.league_id)
        if season is None or league is None:
            raise ValueError("Season membership is missing its season or league")
        summaries.append(SeasonSummary(season.id, season.number, league.tier))
    summaries.sort(key=lambda s: s.number, reverse=True)
    await user_effect(
        session, actor, "club_opened", Surface.CLUB, ownership=ownership, club_id=club.id
    )
    return tuple(summaries)


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
    )
