"""History query/presenter/router tests with supplied objects. No database."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from goaletto.application.club_view import Actor
from goaletto.application.game_actions import PermissionDenied
from goaletto.application.history import (
    get_club_results,
    get_club_seasons,
    get_match_report,
    get_player_matches,
    get_season_archive,
    stored_events,
)
from goaletto.application.match_simulation import demo_match, simulate_match
from goaletto.bot import history_router
from goaletto.bot.history_callbacks import (
    archive_callback,
    is_history_callback,
    match_callback,
    parse_history_callback,
    player_history_callback,
    results_callback,
    seasons_callback,
)
from goaletto.bot.history_cards import (
    archive_view,
    player_history_view,
    report_view,
    results_view,
    seasons_view,
)
from goaletto.domain.match_codec import state_document, state_hash
from goaletto.domain.names import latin_name
from goaletto.infrastructure.models import (
    AnalyticsManager,
    Club,
    Fixture,
    GameActionReceipt,
    League,
    Manager,
    Match,
    MatchResult,
    MatchStage,
    MatchTick,
    OwnershipPeriod,
    Person,
    Player,
    Season,
    SeasonClub,
    Standing,
)

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)

KICKOFF = demo_match(7)
FINAL, _ = simulate_match(KICKOFF)
INITIAL_DOC = state_document(KICKOFF)
TERMINAL_DOC = state_document(FINAL)
HOME_ID = FINAL.home.snapshot.club_id
AWAY_ID = FINAL.away.snapshot.club_id
STARTER_ID = FINAL.home.snapshot.players[0].id
STARTER_NAME = FINAL.home.snapshot.players[0].name


def tick(
    match_id: UUID, tick_number: int, game_minute: int, events: list[dict[str, object]]
) -> MatchTick:
    return MatchTick(
        match_id=match_id,
        tick_number=tick_number,
        game_minute=game_minute,
        due_at=NOW,
        committed_at=NOW,
        events=events,
        state_hash="0" * 64,
    )


def goal_event(minute: int, tick_number: int, person: UUID) -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "kind": "goal",
        "tick": tick_number,
        "minute": minute,
        "side": "home",
        "player_id": str(person),
        "other_player_id": None,
        "detail": "center",
    }


class Rows:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


class Recorder:
    """Supplied rows keyed by test attributes; records statements and receipts."""

    def __init__(self) -> None:
        self.season_id = uuid4()
        self.league_id = uuid4()
        self.manager = Manager(
            id=uuid4(), telegram_id=123, reminders_enabled=True, bot_available=True
        )
        self.club = Club(
            id=HOME_ID,
            league_id=uuid4(),
            name="Переименованный клуб",
            owner_telegram_id=123,
            budget=500000,
            kit_primary="#743D42",
            kit_secondary="#F2E9D8",
            kit_version=1,
            stadium_capacity=5000,
            supporters=2500,
        )
        self.league = League(id=self.league_id, league_set_id=uuid4(), tier=5)
        last = NOW - timedelta(days=1)
        from goaletto.domain.ownership import ownership_deadline

        self.ownership = OwnershipPeriod(
            id=uuid4(),
            operation_key="op",
            manager_id=self.manager.id,
            club_id=self.club.id,
            claimed_at=last - timedelta(days=9),
            last_game_action_at=last,
            expires_at=ownership_deadline(last),
            ended_at=None,
            end_reason=None,
        )
        self.actor = Actor(self.manager.id, 123, None, uuid4(), NOW)
        self.fixture = self._fixture(round_number=3, days_ago=7)
        self.match = self._match(self.fixture)
        self.receipt = self._receipt(self.fixture, self.match)
        self.ticks = [
            tick(self.match.id, 5, 15, [goal_event(15, 5, STARTER_ID)]),
            tick(
                self.match.id,
                9,
                27,
                [
                    {
                        "id": str(uuid4()),
                        "kind": "yellow",
                        "tick": 9,
                        "minute": 27,
                        "side": "home",
                        "player_id": str(STARTER_ID),
                        "other_player_id": None,
                        "detail": None,
                    }
                ],
            ),
        ]
        self.stages = []
        self.fixtures = [self.fixture]
        self.matches = [self.match]
        self.receipts_by_match = {self.match.id: self.receipt}
        self.live_player: Player | None = Player(
            person_id=STARTER_ID,
            club_id=self.club.id,
            position="MC",
            alternative_positions=[],
            goalkeeping=1,
            defending=9,
            playmaking=12,
            winger=12,
            attacking=9,
            set_pieces=10,
            stamina=11,
            pace=10,
            leadership=10,
            loyalty=10,
            form=6,
            experience=0,
            morale=50,
            status="active",
            last_season_id=None,
            retired_after_season_id=None,
            injured_until=None,
        )
        self.person_row: Person | None = Person(
            id=STARTER_ID,
            creation_key="k",
            name=STARTER_NAME,
            age=25,
            career="player",
            appearance_identity_id="face-1",
            appearance_variant_id="face-1-v",
            home_league_set_id=None,
            age_applied_for_season_id=None,
        )
        self.membership = SeasonClub(
            season_id=self.season_id,
            club_id=self.club.id,
            league_id=self.league_id,
            league_set_id=self.league.league_set_id,
            tie_break_order=1,
        )
        self.season = Season(
            id=self.season_id,
            league_set_id=self.league.league_set_id,
            number=2,
            status="active",
            seed=7,
            starts_at=NOW - timedelta(weeks=10),
            ends_at=NOW + timedelta(weeks=10),
            rollover_completed_at=None,
        )
        self.memberships = [self.membership]
        self.standings: list[Standing] = []
        self.clubs = [
            self.club,
            Club(
                id=AWAY_ID,
                league_id=self.league_id,
                name="Маяк",
                owner_telegram_id=None,
                budget=0,
                kit_primary="#000000",
                kit_secondary="#FFFFFF",
                kit_version=1,
                stadium_capacity=5000,
                supporters=1000,
            ),
        ]
        self.receipt_rows: dict[tuple[UUID, int], GameActionReceipt] = {}
        self.statements: list[object] = []
        self.added: list[object] = []
        self.events: list[object] = []

    def _fixture(self, round_number: int, days_ago: int) -> Fixture:
        at = NOW - timedelta(days=days_ago)
        return Fixture(
            id=uuid4(),
            season_id=self.season_id,
            league_id=self.league_id,
            home_club_id=HOME_ID,
            away_club_id=AWAY_ID,
            round_number=round_number,
            scheduled_start_at=at,
            lineup_lock_at=at - timedelta(minutes=1),
            actual_start_at=at,
            actual_finish_at=at + timedelta(minutes=33),
            status="finished",
        )

    def _match(self, fixture: Fixture) -> Match:
        return Match(
            id=uuid4(),
            fixture_id=fixture.id,
            seed=7,
            engine_version="regulation-v1",
            state_version=1,
            state=TERMINAL_DOC,
            starting_snapshot=INITIAL_DOC,
            completed_tick=30,
            phase="full_time",
            home_goals=FINAL.home.stats.goals,
            away_goals=FINAL.away.stats.goals,
            next_due_at=None,
            paused_at=None,
            paused_seconds=0,
        )

    def _receipt(self, fixture: Fixture, match: Match) -> MatchResult:
        return MatchResult(
            match_id=match.id,
            fixture_id=fixture.id,
            season_id=fixture.season_id,
            league_id=fixture.league_id,
            home_club_id=fixture.home_club_id,
            away_club_id=fixture.away_club_id,
            version=1,
            home_goals=match.home_goals,
            away_goals=match.away_goals,
            actual_start_at=fixture.actual_start_at or NOW,
            actual_finish_at=fixture.actual_finish_at or NOW,
            recorded_at=NOW,
            state_hash=state_hash(FINAL),
        )

    def in_transaction(self) -> bool:
        return True

    async def execute(self, statement: object, params: object = None) -> None:
        self.statements.append(statement)

    def _params(self, statement: object) -> dict[str, object]:
        try:
            return dict(statement.compile().params)  # type: ignore[union-attr]
        except Exception:
            return {}

    def _extract_person_id(self, params: dict[str, object]) -> UUID | None:
        for value in params.values():
            if isinstance(value, dict):
                for side in ("home", "away"):
                    try:
                        nested = value[side]  # type: ignore[literal-required]
                        pid = nested["snapshot"]["players"][0]["id"]  # type: ignore[index]
                        return UUID(str(pid))
                    except Exception:
                        continue
        return None

    def _ordered_fixtures(self, fixtures: list[Fixture]) -> list[Fixture]:
        return sorted(fixtures, key=lambda f: (-f.scheduled_start_at.timestamp(), str(f.id)))

    def _club_filtered_fixtures(self, params: dict[str, object]) -> list[Fixture]:
        season_wanted = params.get("season_id_1")
        out = []
        for fixture in self.fixtures:
            if fixture.status != "finished":
                continue
            if self.club.id not in (fixture.home_club_id, fixture.away_club_id):
                # Keep foreign fixtures only for direct id lookups, not listings.
                continue
            if season_wanted is not None and str(fixture.season_id) != str(season_wanted):
                continue
            out.append(fixture)
        return self._ordered_fixtures(out)

    def _player_filtered_fixtures(self, person_id: UUID) -> list[Fixture]:
        from goaletto.domain.match_codec import restore_state as _restore

        match_by_fixture = {m.fixture_id: m for m in self.matches}
        out: list[Fixture] = []
        for fixture in self._club_filtered_fixtures({}):
            match = match_by_fixture.get(fixture.id)
            if match is None:
                continue
            try:
                initial = _restore(match.starting_snapshot)
            except ValueError:
                continue
            for candidate in ("home", "away"):
                team = initial.home if candidate == "home" else initial.away
                if team.snapshot.club_id != self.club.id:
                    continue
                if any(p.id == person_id for p in team.snapshot.players):
                    out.append(fixture)
                    break
        return self._ordered_fixtures(out)

    async def scalar(self, statement: object, params: object = None) -> object:
        self.statements.append(statement)
        if isinstance(statement, TextClause):
            return True
        entity = statement.column_descriptions[0].get("entity")  # type: ignore[union-attr]
        if entity is None:
            # COUNT queries for bounded pagination.
            compiled_params = self._params(statement)
            text = str(statement)
            if "matches" in text or "starting_snapshot" in text:
                person_id = self._extract_person_id(compiled_params)
                if person_id is None:
                    return 0
                return len(self._player_filtered_fixtures(person_id))
            return len(self._club_filtered_fixtures(compiled_params))
        if entity is Manager:
            return self.manager
        if entity is AnalyticsManager:
            return None
        if entity is OwnershipPeriod:
            return self.ownership
        if entity is Club:
            return self.club
        if entity is League:
            return self.league
        if entity is Season:
            return self.season
        if entity is Fixture:
            wanted = self._params(statement).get("id_1")
            if wanted is not None:
                for fixture in self.fixtures:
                    if str(fixture.id) == str(wanted):
                        return fixture
                return None
            return None
        if entity is Match:
            wanted = self._params(statement).get("fixture_id_1")
            if wanted is None:
                return None
            if isinstance(wanted, (list, tuple, set)):
                return None
            for match in self.matches:
                if str(match.fixture_id) == str(wanted):
                    return match
            return None
        if entity is SeasonClub:
            return self.membership
        if entity is Player:
            if self.live_player is None:
                return None
            wanted_person = self._params(statement).get("person_id_1")
            wanted_club = self._params(statement).get("club_id_1")
            if wanted_person is not None and str(self.live_player.person_id) != str(wanted_person):
                return None
            if wanted_club is not None and str(self.live_player.club_id) != str(wanted_club):
                return None
            return self.live_player
        return None

    async def scalars(self, statement: object) -> Rows:
        self.statements.append(statement)
        entity = statement.column_descriptions[0].get("entity")  # type: ignore[union-attr]
        compiled_params = self._params(statement)
        text = str(statement)
        if entity is Fixture:
            if "matches" in text or "starting_snapshot" in text:
                person_id = self._extract_person_id(compiled_params)
                if person_id is None:
                    return Rows([])
                filtered = self._player_filtered_fixtures(person_id)
            else:
                filtered = self._club_filtered_fixtures(compiled_params)
            limit = getattr(statement, "_limit", None)  # type: ignore[union-attr]
            offset = getattr(statement, "_offset", None)  # type: ignore[union-attr]
            start = int(offset or 0)
            end = start + int(limit) if limit is not None else None
            return Rows(filtered[start:end])
        if entity is Match:
            wanted = compiled_params.get("fixture_id_1")
            if isinstance(wanted, (list, tuple, set)):
                wanted_ids = {str(v) for v in wanted}
                return Rows([m for m in self.matches if str(m.fixture_id) in wanted_ids])
            return Rows(list(self.matches))
        if entity is MatchResult:
            wanted = compiled_params.get("fixture_id_1")
            receipts = list(self.receipts_by_match.values())
            if isinstance(wanted, (list, tuple, set)):
                wanted_ids = {str(v) for v in wanted}
                return Rows([r for r in receipts if str(r.fixture_id) in wanted_ids])
            return Rows(receipts)
        if entity is MatchTick:
            wanted = compiled_params.get("match_id_1")
            if isinstance(wanted, (list, tuple, set)):
                wanted_ids = {str(v) for v in wanted}
                return Rows([t for t in self.ticks if str(t.match_id) in wanted_ids])
            if wanted is not None:
                return Rows([t for t in self.ticks if str(t.match_id) == str(wanted)])
            return Rows(list(self.ticks))
        if entity is MatchStage:
            wanted = compiled_params.get("match_id_1")
            if isinstance(wanted, (list, tuple, set)):
                wanted_ids = {str(v) for v in wanted}
                return Rows([s for s in self.stages if str(s.match_id) in wanted_ids])
            if wanted is not None:
                return Rows([s for s in self.stages if str(s.match_id) == str(wanted)])
            return Rows(list(self.stages))
        if entity is SeasonClub:
            if "club_id_1" in compiled_params:
                return Rows(
                    [
                        m
                        for m in self.memberships
                        if str(m.club_id) == str(compiled_params["club_id_1"])
                    ]
                )
            return Rows(list(self.memberships))
        if entity is Standing:
            return Rows(list(self.standings))
        if entity is Club:
            return Rows(list(self.clubs))
        if entity is Season:
            wanted = compiled_params.get("id_1")
            if isinstance(wanted, (list, tuple, set)):
                wanted_ids = {str(v) for v in wanted}
                if hasattr(self, "seasons_list"):
                    return Rows(
                        [s for s in self.seasons_list if str(s.id) in wanted_ids]  # type: ignore[attr-defined]
                    )
                return Rows(
                    [self.season]
                    if str(self.season_id) in wanted_ids or str(self.season.id) in wanted_ids
                    else []
                )
            return Rows([self.season])
        if entity is League:
            wanted = compiled_params.get("id_1")
            if isinstance(wanted, (list, tuple, set)):
                wanted_ids = {str(v) for v in wanted}
                if hasattr(self, "leagues_list"):
                    return Rows(
                        [s for s in self.leagues_list if str(s.id) in wanted_ids]  # type: ignore[attr-defined]
                    )
                return Rows(
                    [self.league]
                    if str(self.league_id) in wanted_ids or str(self.league.id) in wanted_ids
                    else []
                )
            return Rows([self.league])
        return Rows([])

    async def get(self, model: object, key: object) -> object:
        if model is Manager:
            return self.manager
        if model is MatchResult:
            return self.receipts_by_match.get(cast(UUID, key))
        if model is GameActionReceipt:
            return self.receipt_rows.get(cast(tuple[UUID, int], key))
        if model is Season:
            return self.season if str(key) == str(self.season_id) else None
        if model is League:
            return self.league
        if model is Person:
            if self.person_row is None:
                return None
            return self.person_row if str(key) == str(self.person_row.id) else None
        return None

    def add(self, value: object) -> None:
        self.added.append(value)
        if isinstance(value, GameActionReceipt):
            self.receipt_rows[(value.operation_id, value.effect_number)] = value

    async def flush(self) -> None:
        pass

    @asynccontextmanager
    async def begin_nested(self) -> object:
        yield


@pytest.fixture(autouse=True)
def events(monkeypatch: pytest.MonkeyPatch) -> None:
    async def record(session: Recorder, event: object) -> bool:
        session.events.append(event)
        return True

    monkeypatch.setattr("goaletto.application.game_actions.record_event", record)


def session(recorder: Recorder) -> AsyncSession:
    return cast(AsyncSession, recorder)


def test_history_callbacks_compact_and_reject_legacy() -> None:
    fixture, person, season = uuid4(), uuid4(), uuid4()
    for value in (
        results_callback(3),
        match_callback(fixture, 2),
        player_history_callback(person, 1),
        seasons_callback(1),
        archive_callback(season),
    ):
        assert len(value.encode()) <= 64
    assert parse_history_callback(results_callback(3)) == ("results", None, 3)
    assert parse_history_callback(match_callback(fixture, 2)) == ("match", fixture, 2)
    assert parse_history_callback(player_history_callback(person, 1)) == ("player", person, 1)
    assert parse_history_callback(seasons_callback(1)) == ("seasons", None, 1)
    assert parse_history_callback(archive_callback(season)) == ("archive", season, 1)
    for bad in (
        "",
        "hx:",
        "hx:r:0",
        "hx:m:bad:1",
        "hx:pm::1",
        "hx:a:bad",
        "hx:zzz",
        "preview:squad",
        "game:club",
        "table:a:b:1",
        "report:abc",
        "vc:c",
        "vc:s:1",
        "vc:p:abc:1",
    ):
        with pytest.raises(ValueError):
            parse_history_callback(bad)
    assert not is_history_callback("vc:c") and is_history_callback(results_callback(1))


async def test_club_results_paginate_and_record_receipt() -> None:
    recorder = Recorder()
    for round_number, days_ago in ((4, 1), (2, 14), (1, 21)):
        fixture = recorder._fixture(round_number, days_ago)
        match = recorder._match(fixture)
        recorder.fixtures.append(fixture)
        recorder.matches.append(match)
        recorder.receipts_by_match[match.id] = recorder._receipt(fixture, match)
    recorder.fixtures.sort(key=lambda f: f.scheduled_start_at, reverse=True)
    page = await get_club_results(session(recorder), recorder.actor, 1)
    assert page.total == 4 and page.total_pages == 1
    assert [e.round_number for e in page.entries] == [4, 3, 2, 1]
    assert all(e.home_goals == 1 and e.away_goals == 2 for e in page.entries)
    assert page.entries[0].outcome == "loss"  # owned club is home, lost 1:2
    assert len(recorder.receipt_rows) == 1
    text, keyboard = results_view(page)
    assert "Yantar 1:2 Mayak" in text  # list preserves the kickoff names
    assert any(
        b.callback_data == match_callback(page.entries[0].fixture_id, 1)
        for row in keyboard.inline_keyboard
        for b in row
    )


async def test_results_need_current_ownership_and_receipt() -> None:
    recorder = Recorder()
    recorder.ownership = None  # type: ignore[assignment]
    with pytest.raises(PermissionDenied):
        await get_club_results(session(recorder), recorder.actor, 1)
    with pytest.raises(PermissionDenied):
        await get_match_report(session(recorder), recorder.actor, recorder.fixture.id)
    recorder = Recorder()
    recorder.receipts_by_match = {}
    with pytest.raises(ValueError, match="receipt"):
        await get_club_results(session(recorder), recorder.actor, 1)
    with pytest.raises(ValueError, match="receipt"):
        await get_match_report(session(recorder), recorder.actor, recorder.fixture.id)


async def test_match_report_uses_frozen_snapshot_not_live_names() -> None:
    recorder = Recorder()
    report = await get_match_report(session(recorder), recorder.actor, recorder.fixture.id)
    assert (report.home_goals, report.away_goals) == (1, 2)
    assert report.home_name == "Янтарь"  # frozen; live club was renamed
    assert report.possession_home_pct + (100 - report.possession_home_pct) == 100
    kinds = [e.kind for e in report.events]
    assert "goal" in kinds and "yellow" in kinds
    starter = next(p for p in report.home_lineup if p.person_id == STARTER_ID)
    assert starter.started and starter.minutes == 90 and starter.goals == 1
    assert starter.age == 25 and starter.name == STARTER_NAME
    text, keyboard = report_view(report, 1)
    assert "Yantar 1:2 Mayak" in text and "⚽ 15′" in text and "🟨 27′" in text
    assert latin_name(STARTER_NAME) in text
    assert any(b.callback_data == "hx:r:1" for row in keyboard.inline_keyboard for b in row)


async def test_foreign_or_unfinished_fixture_has_no_report() -> None:
    recorder = Recorder()
    other = recorder._fixture(5, 2)
    other.home_club_id, other.away_club_id = uuid4(), uuid4()
    recorder.fixtures.append(other)
    with pytest.raises(PermissionDenied):
        await get_match_report(session(recorder), recorder.actor, other.id)
    recorder.fixture.status = "live"
    with pytest.raises(ValueError):
        await get_match_report(session(recorder), recorder.actor, recorder.fixture.id)


async def test_player_history_from_snapshots_minutes_and_goals() -> None:
    recorder = Recorder()
    page = await get_player_matches(session(recorder), recorder.actor, STARTER_ID, 1)
    assert page.total == 1 and page.person_name == STARTER_NAME
    entry = page.entries[0]
    assert entry.minutes == 90 and entry.goals == 1 and entry.side == "home"
    text, _ = player_history_view(page)
    assert "90′" in text and "⚽1" in text
    # Retired footballer (no live Player row) stays readable in history.
    recorder.live_player = None
    page = await get_player_matches(
        session(recorder),
        Actor(recorder.manager.id, 123, None, uuid4(), NOW),
        STARTER_ID,
        1,
    )
    assert page.total == 1
    with pytest.raises(PermissionDenied):
        await get_player_matches(
            session(recorder),
            Actor(recorder.manager.id, 123, None, uuid4(), NOW),
            uuid4(),
            1,
        )


async def test_opponent_is_not_part_of_owned_club_player_history() -> None:
    recorder = Recorder()
    recorder.live_player = None
    opponent = FINAL.away.snapshot.players[0].id
    with pytest.raises(PermissionDenied):
        await get_player_matches(session(recorder), recorder.actor, opponent, 1)


async def test_halftime_stage_events_are_included_once_in_stored_order() -> None:
    recorder = Recorder()
    duplicate = recorder.ticks[0].events[0]
    substitution = {
        "id": str(uuid4()),
        "kind": "substitution",
        "tick": 15,
        "minute": 45,
        "side": "home",
        "player_id": str(STARTER_ID),
        "other_player_id": str(uuid4()),
        "detail": None,
    }
    recorder.stages = [
        MatchStage(
            match_id=recorder.match.id,
            stage="second_half_start",
            occurred_at=NOW,
            events=[duplicate, substitution],
            state_hash="0" * 64,
        )
    ]
    events = await stored_events(session(recorder), recorder.match.id)
    assert [event["kind"] for event in events] == ["goal", "yellow", "substitution"]


async def test_archive_uses_membership_and_stored_counters() -> None:
    recorder = Recorder()
    others = [uuid4() for _ in range(9)]
    recorder.clubs.extend(
        Club(
            id=cid,
            league_id=recorder.league_id,
            name=f"Клуб {i}",
            owner_telegram_id=None,
            budget=0,
            kit_primary="#000000",
            kit_secondary="#FFFFFF",
            kit_version=1,
            stadium_capacity=5000,
            supporters=1000,
        )
        for i, cid in enumerate(others)
    )
    recorder.memberships.extend(
        SeasonClub(
            season_id=recorder.season_id,
            club_id=cid,
            league_id=recorder.league_id,
            league_set_id=recorder.league.league_set_id,
            tie_break_order=i + 2,
        )
        for i, cid in enumerate(others)
    )
    table_in = [(3, 2, 1, 0, 9, 3), (3, 1, 2, 0, 5, 5), (3, 1, 1, 1, 4, 4)]
    recorder.standings.append(
        Standing(
            season_id=recorder.season_id,
            club_id=recorder.club.id,
            played=3,
            won=2,
            drawn=1,
            lost=0,
            goals_for=9,
            goals_against=3,
            points=7,
        )
    )
    for i, cid in enumerate(others):
        played, won, drawn, lost, scored, conceded = table_in[i % 3]
        recorder.standings.append(
            Standing(
                season_id=recorder.season_id,
                club_id=cid,
                played=played,
                won=won,
                drawn=drawn,
                lost=lost,
                goals_for=scored,
                goals_against=conceded,
                points=won * 3 + drawn,
            )
        )
    # Live league moves after the season; the archive must keep the frozen tier.
    recorder.club.league_id = uuid4()
    archive = await get_season_archive(session(recorder), recorder.actor, recorder.season_id)
    assert archive.tier == 5 and archive.number == 2 and len(archive.table) == 10
    assert archive.club_position == 1 and archive.points == 7
    text, _ = archive_view(archive)
    assert "1-е место" in text and "7 очков" in text
    seasons = await get_club_seasons(
        session(recorder), Actor(recorder.manager.id, 123, None, uuid4(), NOW)
    )
    assert len(seasons) == 1 and seasons[0].tier == 5
    text, keyboard = seasons_view(seasons)
    assert "Сезон 2" in text
    assert any(
        b.callback_data == archive_callback(recorder.season_id)
        for row in keyboard.inline_keyboard
        for b in row
    )


async def test_report_survives_restart_with_same_rows() -> None:
    first = Recorder()
    report = await get_match_report(session(first), first.actor, first.fixture.id)
    text_before, _ = report_view(report, 1)
    second = Recorder()  # fresh objects, same persisted documents
    again = await get_match_report(session(second), second.actor, second.fixture.id)
    text_after, _ = report_view(again, 1)
    assert text_before == text_after


async def test_history_router_answers_first_and_handles_stale() -> None:
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from aiogram.types import CallbackQuery, Chat, Message, User

    message = Message(message_id=1, date=_dt.now(_UTC), chat=Chat(id=1, type="private"))
    calls: list[str] = []

    async def answer(*args: object, **kwargs: object) -> None:
        calls.append("answer")

    object.__setattr__(message, "answer", answer)
    edits: list[str] = []

    async def edit_text(text: str, reply_markup: object = None) -> None:
        edits.append(text)

    object.__setattr__(message, "edit_text", edit_text)
    query = CallbackQuery(
        id="q",
        from_user=User(id=123, is_bot=False, first_name="T"),
        chat_instance="ci",
        data="hx:r:1",
        message=message,
    )
    object.__setattr__(query, "answer", answer)

    touched: list[str] = []

    class TouchSession(Recorder):
        def in_transaction(self) -> bool:
            touched.append("db")
            assert calls == ["answer"]
            return True

    class DatabaseStub:
        @asynccontextmanager
        async def transaction(self):
            yield cast(AsyncSession, TouchSession())

    await history_router.history_screen(query, DatabaseStub())
    assert calls == ["answer"] and touched and len(edits) == 1
    assert "Yantar 1:2 Mayak" in edits[0]
