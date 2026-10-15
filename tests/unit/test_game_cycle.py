from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from goaletto.domain.calendar import league_calendar
from goaletto.domain.finances import accrued_wage
from goaletto.domain.game_cycle import (
    Phase,
    cycle_on,
    next_first_league,
    season_end,
    season_epoch,
)
from goaletto.domain.market import market_window
from goaletto.domain.world import WorldOptions

FIRST = datetime(2026, 10, 20, 18, tzinfo=UTC)  # Tuesday, no weekday dependency.


def test_eighteen_four_day_rounds_and_eight_day_offseason():
    options = WorldOptions(key="four-day", seed=7, first_kickoff=FIRST)
    assert options.first_kickoff == FIRST
    epoch = season_epoch(FIRST)
    assert epoch == date(2026, 10, 17)
    assert [cycle_on(epoch, epoch + timedelta(days=n)).phase for n in range(4)] == list(Phase)
    fixtures = league_calendar(
        season_id=UUID(int=1),
        league_id=UUID(int=2),
        clubs=tuple(UUID(int=n) for n in range(10, 20)),
        first_kickoff=FIRST,
        seed=7,
    )
    assert len(fixtures) == 90
    assert len({(f.home_club_id, f.away_club_id) for f in fixtures}) == 90
    assert {f.kickoff for f in fixtures} == {FIRST + timedelta(days=4 * n) for n in range(18)}
    end = season_end(FIRST)
    assert (end.date() - epoch).days == 72
    next_first = next_first_league(FIRST, end, end)
    assert (season_epoch(next_first) - epoch).days == 80
    assert (season_epoch(next_first) - end.date()).days == 8
    assert next_first_league(FIRST, end, end + timedelta(days=8)) > next_first


def test_cycle_transfer_windows_and_explicit_wage_units():
    end = season_end(FIRST)
    for offset in (0, 7):
        at = end + timedelta(days=offset)
        window = market_window(FIRST, end, at)
        assert window.contains(at) and window.closes_at == end + timedelta(days=8)
    assert accrued_wage(700, 2 * 86400, 604800) == 200
    assert accrued_wage(700, 2 * 86400, 345600) == 350
