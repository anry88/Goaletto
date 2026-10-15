"""Four-day UTC tournament cycles, explicit epochs, no clocks or persistence."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum

CYCLE_CALENDAR = "four-day-calendar-v1"
CYCLE_DAYS = 4
CYCLE_SECONDS = 345600
SEASON_CYCLES = 18
OFFSEASON_CYCLES = 2  # Latest user decision: 18 + 2 cycles = 80 days.
HORIZON_DAYS = 10


class Phase(StrEnum):
    CUP = "cup"
    CHAMPIONS = "champions"
    FREE = "free"
    LEAGUE = "league"


@dataclass(frozen=True)
class GameCycle:
    epoch: date
    index: int
    phase: Phase

    @property
    def starts_on(self) -> date:
        return self.epoch + timedelta(days=self.index * CYCLE_DAYS)

    @property
    def id(self) -> str:
        return self.starts_on.isoformat()


def cycle_on(epoch: date, day: date) -> GameCycle:
    index, phase = divmod((day - epoch).days, CYCLE_DAYS)
    return GameCycle(epoch, index, tuple(Phase)[phase])


def midnight(day: date) -> datetime:
    return datetime.combine(day, time(), UTC)


def season_epoch(first_league: datetime) -> date:
    return first_league.date() - timedelta(days=3)


def season_end(first_league: datetime) -> datetime:
    return midnight(season_epoch(first_league) + timedelta(days=SEASON_CYCLES * CYCLE_DAYS))


def next_first_league(first: datetime, finished: datetime, at: datetime) -> datetime:
    """Start a full eight-day offseason after the last final, then a fresh cycle.

    Delayed rollover shifts by complete cycles; never starts a season in the past.
    The first league match is phase four of the new season.
    """
    nominal = season_end(first) + timedelta(days=CYCLE_DAYS * OFFSEASON_CYCLES)
    earliest = midnight(
        max(finished.date(), at.date()) + timedelta(days=CYCLE_DAYS * OFFSEASON_CYCLES)
    )
    while nominal < earliest:
        nominal += timedelta(days=CYCLE_DAYS)
    return nominal + timedelta(days=3, hours=18)
