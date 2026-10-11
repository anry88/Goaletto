"""Regulation clock. Every tick completes three game minutes."""

from dataclasses import dataclass
from enum import StrEnum


class MatchPhase(StrEnum):
    FIRST_HALF = "first_half"
    HALF_TIME = "half_time"
    SECOND_HALF = "second_half"
    FULL_TIME = "full_time"


@dataclass(frozen=True)
class MatchClock:
    tick_seconds: int = 60
    ticks_per_half: int = 15
    game_minutes_per_tick: int = 3
    halftime_seconds: int = 180

    @property
    def total_ticks(self) -> int:
        return 2 * self.ticks_per_half

    @property
    def duration_seconds(self) -> int:
        return self.total_ticks * self.tick_seconds + self.halftime_seconds

    def tick_elapsed_seconds(self, tick: int) -> int:
        if not 1 <= tick <= self.total_ticks:
            raise ValueError("Tick must be between 1 and 30")
        pause = self.halftime_seconds if tick > self.ticks_per_half else 0
        return tick * self.tick_seconds + pause

    def game_minute(self, tick: int) -> int:
        if not 0 <= tick <= self.total_ticks:
            raise ValueError("Tick must be between 0 and 30")
        return tick * self.game_minutes_per_tick

    def phase_at(self, elapsed_seconds: int) -> MatchPhase:
        if elapsed_seconds < 0:
            raise ValueError("Elapsed seconds must not be negative")
        halftime_start = self.ticks_per_half * self.tick_seconds
        second_half_start = halftime_start + self.halftime_seconds
        if elapsed_seconds < halftime_start:
            return MatchPhase.FIRST_HALF
        if elapsed_seconds < second_half_start:
            return MatchPhase.HALF_TIME
        if elapsed_seconds < self.duration_seconds:
            return MatchPhase.SECOND_HALF
        return MatchPhase.FULL_TIME
