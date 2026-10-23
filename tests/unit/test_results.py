from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4, uuid5

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.match_simulation import demo_match, simulate_match
from goaletto.application.results import finalize_result
from goaletto.bot.result_views import (
    compact_id,
    expand_id,
    parse_table_callback,
    result_keyboard,
    result_view,
    table_view,
)
from goaletto.domain.match_codec import state_document, state_hash
from goaletto.domain.standings import LeagueStanding, add_result, rank_league
from goaletto.infrastructure.models import Fixture, Match, MatchResult, SeasonClub, Standing

NOW = datetime(2026, 10, 17, 18, tzinfo=UTC)


def league():
    return tuple(LeagueStanding(uuid5(UUID(int=0), str(i)), f"Club {i}", i) for i in range(1, 11))


@pytest.mark.parametrize("score,points", [((2, 0), 3), ((1, 1), 1), ((0, 2), 0)])
def test_result_points_and_immutable_counters(score, points):
    initial = league()[0]
    new = add_result(initial, *score)
    assert initial.played == initial.points == 0
    assert (
        new.played == 1
        and new.points == points
        and new.goals_for == score[0]
        and new.goals_against == score[1]
    )


def test_order_uses_points_difference_scored_then_persisted_preseason_draw():
    rows = league()
    assert rank_league(tuple(reversed(rows))) == rows
    high_points = replace(rows[3], played=1, won=1, goals_for=1, goals_against=0)
    high_diff = replace(rows[2], played=1, won=1, goals_for=3, goals_against=0)
    high_scored = replace(rows[1], played=1, won=1, goals_for=4, goals_against=1)
    tied = replace(rows[0], played=1, won=1, goals_for=4, goals_against=1)
    ranked = rank_league((tied, high_scored, high_diff, high_points, *rows[4:]))
    assert ranked[:4] == (tied, high_scored, high_diff, high_points)
    with pytest.raises(ValueError):
        rank_league((rows[0], *rows[:9]))


@pytest.fixture(autouse=True)
def money_io(monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr("goaletto.application.game_actions.allocation_lock", AsyncMock())
    monkeypatch.setattr("goaletto.application.finances.match_income", AsyncMock())
    monkeypatch.setattr("goaletto.application.match_development.apply_development", AsyncMock())
    monkeypatch.setattr("goaletto.application.discipline.apply_discipline", AsyncMock())


class Rows:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class Recorder:
    def __init__(self, fixture, match, standings, members):
        self.fixture = fixture
        self.match = match
        self.standings = standings
        self.members = members
        self.receipt = None
        self.added = []
        self.statements = []
        self.flushes = 0

    def in_transaction(self):
        return True

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.fixture if statement.column_descriptions[0]["entity"] is Fixture else self.match

    async def get(self, model, key):
        return self.receipt

    async def scalars(self, statement):
        self.statements.append(statement)
        return Rows(
            self.members
            if statement.column_descriptions[0]["entity"] is SeasonClub
            else self.standings
        )

    def add(self, value):
        self.added.append(value)
        self.receipt = value

    async def flush(self):
        self.flushes += 1


def completed():
    state, _ = simulate_match(demo_match(7))
    fixture = Fixture(
        id=uuid4(),
        season_id=uuid4(),
        league_id=uuid4(),
        home_club_id=state.home.snapshot.club_id,
        away_club_id=state.away.snapshot.club_id,
        status="finished",
        actual_start_at=NOW,
        actual_finish_at=NOW + timedelta(minutes=33),
    )
    match = Match(
        id=state.match_id,
        state=state_document(state),
        phase="full_time",
        completed_tick=30,
        seed=state.seed,
        engine_version=state.engine_version,
        state_version=1,
        home_goals=state.home.stats.goals,
        away_goals=state.away.stats.goals,
    )
    standings = [
        Standing(
            season_id=fixture.season_id,
            club_id=club,
            played=0,
            won=0,
            drawn=0,
            lost=0,
            goals_for=0,
            goals_against=0,
            points=0,
        )
        for club in (fixture.home_club_id, fixture.away_club_id)
    ]
    members = [
        SeasonClub(
            season_id=fixture.season_id,
            club_id=s.club_id,
            league_id=fixture.league_id,
            tie_break_order=i,
        )
        for i, s in enumerate(standings, 1)
    ]
    return state, Recorder(fixture, match, standings, members)


async def test_finalize_receipt_prevents_repeat_points_and_locks_standings_in_stable_order():
    state, recorder = completed()
    assert await finalize_result(
        cast(AsyncSession, recorder), recorder.fixture.id, NOW + timedelta(minutes=34)
    )
    h, a = recorder.standings
    assert (h.played, h.lost, h.goals_for, h.goals_against, h.points) == (1, 1, 1, 2, 0)
    assert (a.played, a.won, a.goals_for, a.goals_against, a.points) == (1, 1, 2, 1, 3)
    assert recorder.receipt.state_hash == state_hash(state) and recorder.flushes == 1
    assert not await finalize_result(
        cast(AsyncSession, recorder), recorder.fixture.id, NOW + timedelta(minutes=40)
    )
    assert len(recorder.added) == 1 and h.played == a.played == 1
    sql = str(recorder.statements[3].compile(dialect=postgresql.dialect()))
    assert "ORDER BY standings.club_id FOR UPDATE" in sql


async def test_changed_or_abandoned_result_never_assigns_points():
    _, recorder = completed()
    recorder.fixture.status = "abandoned"
    with pytest.raises(ValueError, match="completed regulation"):
        await finalize_result(
            cast(AsyncSession, recorder), recorder.fixture.id, NOW + timedelta(minutes=34)
        )
    assert all(s.played == 0 for s in recorder.standings)
    _, recorder = completed()
    recorder.receipt = MatchResult(state_hash="0" * 64)
    with pytest.raises(ValueError, match="changed"):
        await finalize_result(
            cast(AsyncSession, recorder), recorder.fixture.id, NOW + timedelta(minutes=34)
        )
    assert recorder.added == []


def test_full_table_and_report_buttons_fit_callbacks_and_escape_html():
    rows = league()
    season, league_id = uuid4(), uuid4()
    rows = (replace(rows[0], name="<b>Club&</b>"), *rows[1:])
    text, kb = table_view(rows, season, league_id, 5)
    assert "&lt;b&gt;Club&amp;&lt;/b&gt;" in text
    assert "Club 6" in text and "Club 10" in text and "↓" not in text
    assert "Очки:" in text and "Матчи:" in text and "Голы:" in text
    assert "1/2" not in text and "2/2" not in text
    callback = kb.inline_keyboard[0][0].callback_data
    assert len(callback.encode()) <= 64
    assert parse_table_callback(callback) == (season, league_id, 1)
    assert kb.inline_keyboard[0][0].text == "🔄 Обновить"
    for tier in (1, 3, 5):
        first, _ = table_view(rows, season, league_id, tier)
        old_second, _ = table_view(rows, season, league_id, tier, 2)
        assert first == old_second
        assert sum(f"<b>{place}. " in first for place in range(1, 11)) == 10
        assert ("↑" in first) == (tier > 1)
        assert ("↓" in first) == (tier < 5)
        assert len(first.encode("utf-16-le")) // 2 < 4096
    state, _ = simulate_match(demo_match(7))
    assert "Финальный свисток" in result_view(state) and "1:2" in result_view(state)
    assert len(result_keyboard(uuid4()).inline_keyboard[0][0].callback_data.encode()) <= 64
    assert expand_id(compact_id(season)) == season
    for bad in ("bad", compact_id(season) + "=", "!" * 22):
        with pytest.raises(ValueError):
            expand_id(bad)
    with pytest.raises(ValueError):
        table_view(rows, season, league_id, 1, True)


@pytest.fixture(autouse=True)
def recovery_boundary(monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr("goaletto.application.recovery.finish_energy", AsyncMock())
