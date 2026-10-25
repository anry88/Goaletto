"""Pure form signals and elapsed-time inactivity; all time is supplied explicitly."""

from dataclasses import dataclass
from datetime import datetime, timedelta

IDLE_GRACE = 3 * 86400
IDLE_STEP = 2 * 86400


@dataclass(frozen=True)
class MatchPerformance:
    minutes: int
    energy_bps: int = 10000
    goals: int = 0
    assists: int = 0
    saves: int = 0
    yellows: int = 0
    dismissed: bool = False
    player_of_match: bool = False

    def __post_init__(self) -> None:
        if (
            any(
                type(v) is not int or v < 0
                for v in (
                    self.minutes,
                    self.energy_bps,
                    self.goals,
                    self.assists,
                    self.saves,
                    self.yellows,
                )
            )
            or self.minutes > 120
            or self.energy_bps > 10000
            or self.yellows > 2
            or type(self.dismissed) is not bool
            or type(self.player_of_match) is not bool
            or self.minutes == 0
            and (self.goals or self.assists or self.saves or self.player_of_match)
        ):
            raise ValueError("Invalid match performance")


def performance_chances(form: int, performance: MatchPerformance) -> tuple[int, int]:
    if type(form) is not int or not 1 <= form <= 10:
        raise ValueError("Invalid form")
    if performance.minutes == 0:
        return 0, 0
    merit = min(
        6500, performance.goals * 3000 + performance.assists * 1800 + performance.saves * 500
    )
    merit += 1800 if performance.player_of_match else 0
    fatigue = max(0, 4000 - performance.energy_bps) * 2
    up = (
        performance.minutes * 40 * (11 - form) // 10
        + merit
        - performance.yellows * 800
        - fatigue // 2
    )
    down = (form - 1) * 200 - merit // 4 + performance.yellows * 1200 + fatigue
    down += max(0, performance.minutes - 90) * 30
    if performance.dismissed:
        up, down = 0, down + 4000
    if performance.energy_bps < 2000:
        up = 0
    up = max(0, min(9000, up)) if form < 10 else 0
    down = max(0, min(10000 - up, down)) if form > 1 else 0
    if (
        performance.player_of_match
        and not performance.dismissed
        and performance.energy_bps >= 4000
        and form < 10
    ):
        up, down = 10000, 0
    return up, down


def idle_form(
    form: int, activity_at: datetime, applied_steps: int, at: datetime
) -> tuple[int, int]:
    if (
        type(form) is not int
        or not 1 <= form <= 10
        or type(applied_steps) is not int
        or applied_steps < 0
        or any(t.tzinfo is None or t.utcoffset() != timedelta(0) for t in (activity_at, at))
    ):
        raise ValueError("Invalid form activity clock")
    steps = max(0, int((at - activity_at).total_seconds()) - IDLE_GRACE) // IDLE_STEP
    return max(1, form - max(0, steps - applied_steps)), max(steps, applied_steps)
