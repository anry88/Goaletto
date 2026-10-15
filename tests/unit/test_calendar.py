from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

import pytest

from goaletto.domain.calendar import (
    fixture_work,
    league_calendar,
    next_season_kickoff,
    season_finish_at,
)

SEASON = UUID("11ee8a0b-69b1-4ae9-a2cc-af6f4dbdd69e")
LEAGUE = uuid5(SEASON, "league")
CLUBS = tuple(uuid5(LEAGUE, str(i)) for i in range(10))
KICKOFF = datetime(2026, 10, 17, 18, tzinfo=UTC)


@pytest.mark.parametrize("seed", [0, 1, 12, 2**63 - 1])
def test_double_round_robin_is_complete_without_club_conflicts(seed: int) -> None:
    fixtures = league_calendar(
        season_id=SEASON, league_id=LEAGUE, clubs=CLUBS, first_kickoff=KICKOFF, seed=seed
    )
    assert len(fixtures) == len({f.id for f in fixtures}) == 90
    assert len({(f.home_club_id, f.away_club_id) for f in fixtures}) == 90
    for round_number in range(1, 19):
        games = [f for f in fixtures if f.round_number == round_number]
        assert len(games) == 5
        assert Counter(c for f in games for c in (f.home_club_id, f.away_club_id)) == dict.fromkeys(
            CLUBS, 1
        )
        assert {f.kickoff for f in games} == {KICKOFF + timedelta(days=4 * (round_number - 1))}
    assert Counter(f.home_club_id for f in fixtures) == dict.fromkeys(CLUBS, 9)
    assert Counter(f.away_club_id for f in fixtures) == dict.fromkeys(CLUBS, 9)
    assert fixtures == league_calendar(
        season_id=SEASON,
        league_id=LEAGUE,
        clubs=tuple(reversed(CLUBS)),
        first_kickoff=KICKOFF,
        seed=seed,
    )


def test_minute_deadlines_halftime_and_pre_match_actions() -> None:
    fixture = league_calendar(
        season_id=SEASON, league_id=LEAGUE, clubs=CLUBS, first_kickoff=KICKOFF, seed=0
    )[0]
    jobs = fixture_work(fixture)
    assert len(jobs) == len({job.dedupe_key for job in jobs}) == 34
    ticks = {job.tick_number: job.due_at for job in jobs if job.kind == "match.tick"}
    assert ticks[1] == KICKOFF + timedelta(minutes=1)
    assert ticks[15] == KICKOFF + timedelta(minutes=15)
    assert ticks[16] == KICKOFF + timedelta(minutes=19)
    assert ticks[30] == KICKOFF + timedelta(minutes=33)
    assert next(
        j for j in jobs if j.kind == "match.second_half_start"
    ).due_at == KICKOFF + timedelta(minutes=18)
    assert fixture.reminder_at == KICKOFF - timedelta(minutes=5)
    assert fixture.lineup_lock_at == KICKOFF - timedelta(minutes=1)


def test_two_empty_weekends_before_next_season_and_utc_survives_dst() -> None:
    final_kickoff = KICKOFF + timedelta(days=68)
    assert season_finish_at(KICKOFF) == (final_kickoff + timedelta(days=1)).replace(hour=0)
    assert next_season_kickoff(KICKOFF) == KICKOFF + timedelta(days=80)
    fixtures = league_calendar(
        season_id=SEASON, league_id=LEAGUE, clubs=CLUBS, first_kickoff=KICKOFF, seed=0
    )
    assert all(f.kickoff.hour == 18 and f.kickoff.utcoffset() == timedelta(0) for f in fixtures)


@pytest.mark.parametrize(
    "kickoff",
    [
        KICKOFF.replace(tzinfo=None),
        KICKOFF + timedelta(seconds=1),
        KICKOFF + timedelta(microseconds=1),
    ],
)
def test_invalid_kickoff_is_rejected(kickoff: datetime) -> None:
    with pytest.raises(ValueError):
        league_calendar(
            season_id=SEASON, league_id=LEAGUE, clubs=CLUBS, first_kickoff=kickoff, seed=0
        )


def test_duplicate_club_is_rejected() -> None:
    with pytest.raises(ValueError):
        league_calendar(
            season_id=SEASON,
            league_id=LEAGUE,
            clubs=CLUBS[:-1] + (CLUBS[0],),
            first_kickoff=KICKOFF,
            seed=0,
        )
