"""Pure four-day league calendar. No clock reads, database or Telegram calls."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from random import Random
from uuid import UUID, uuid5

from goaletto.domain.clock import MatchClock
from goaletto.domain.game_cycle import CYCLE_CALENDAR

CALENDAR_VERSION = CYCLE_CALENDAR


def validate_kickoff(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("Kickoff must be timezone-aware UTC")
    if value.second or value.microsecond:
        raise ValueError("Kickoff must use whole minutes")


@dataclass(frozen=True)
class ScheduledFixture:
    id: UUID
    season_id: UUID
    league_id: UUID
    round_number: int
    home_club_id: UUID
    away_club_id: UUID
    kickoff: datetime

    @property
    def lineup_lock_at(self) -> datetime:
        return self.kickoff - timedelta(minutes=1)

    @property
    def reminder_at(self) -> datetime:
        return self.kickoff - timedelta(minutes=5)


@dataclass(frozen=True)
class ScheduledWork:
    dedupe_key: str
    kind: str
    due_at: datetime
    fixture_id: UUID | None = None
    tick_number: int | None = None


def league_calendar(
    *,
    season_id: UUID,
    league_id: UUID,
    clubs: tuple[UUID, ...],
    first_kickoff: datetime,
    seed: int,
) -> tuple[ScheduledFixture, ...]:
    validate_kickoff(first_kickoff)
    if len(clubs) != 10 or len(set(clubs)) != 10:
        raise ValueError("Calendar needs ten distinct clubs")
    if not all(isinstance(club, UUID) for club in clubs):
        raise ValueError("Club identifiers must be UUIDs")
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("Seed must fit a nonnegative BIGINT")
    order = sorted(clubs, key=str)
    Random(seed).shuffle(order)
    fixtures: list[ScheduledFixture] = []
    for round_index in range(9):
        for pair_index in range(5):
            home, away = order[pair_index], order[9 - pair_index]
            if (round_index + pair_index) % 2:
                home, away = away, home
            for offset, left, right in ((0, home, away), (9, away, home)):
                round_number = round_index + offset + 1
                fixtures.append(
                    ScheduledFixture(
                        id=uuid5(season_id, f"{league_id}:{left}:{right}"),
                        season_id=season_id,
                        league_id=league_id,
                        round_number=round_number,
                        home_club_id=left,
                        away_club_id=right,
                        kickoff=first_kickoff + timedelta(days=4 * (round_number - 1)),
                    )
                )
        order = [order[0], order[-1], *order[1:-1]]
    return tuple(sorted(fixtures, key=lambda f: (f.round_number, str(f.id))))


def fixture_work(fixture: ScheduledFixture) -> tuple[ScheduledWork, ...]:
    clock = MatchClock()
    prefix = f"calendar:v1:fixture:{fixture.id}"
    work = [
        ScheduledWork(f"{prefix}:remind", "match.remind", fixture.reminder_at, fixture.id),
        ScheduledWork(f"{prefix}:lock", "match.lock_lineup", fixture.lineup_lock_at, fixture.id),
        ScheduledWork(f"{prefix}:start", "match.start", fixture.kickoff, fixture.id),
        ScheduledWork(
            f"{prefix}:second-half",
            "match.second_half_start",
            fixture.kickoff + timedelta(seconds=1080),
            fixture.id,
        ),
    ]
    work.extend(
        ScheduledWork(
            f"{prefix}:tick:{tick}",
            "match.tick",
            fixture.kickoff + timedelta(seconds=clock.tick_elapsed_seconds(tick)),
            fixture.id,
            tick,
        )
        for tick in range(1, 31)
    )
    return tuple(work)


def season_finish_at(first_kickoff: datetime) -> datetime:
    from goaletto.domain.game_cycle import season_end

    validate_kickoff(first_kickoff)
    return season_end(first_kickoff)


def next_season_kickoff(first_kickoff: datetime) -> datetime:
    from goaletto.domain.game_cycle import next_first_league

    end = season_finish_at(first_kickoff)
    return next_first_league(first_kickoff, end, end)
