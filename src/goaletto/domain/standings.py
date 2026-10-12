"""Pure result increments and deterministic final league ordering."""

from dataclasses import dataclass, replace
from uuid import UUID


@dataclass(frozen=True)
class LeagueStanding:
    club_id: UUID
    name: str
    tie_break_order: int
    played: int = 0
    won: int = 0
    drawn: int = 0
    lost: int = 0
    goals_for: int = 0
    goals_against: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.club_id, UUID) or not self.name.strip():
            raise ValueError("Standing needs club identity")
        if any(
            type(v) is not int or v < 0
            for v in (
                self.played,
                self.won,
                self.drawn,
                self.lost,
                self.goals_for,
                self.goals_against,
            )
        ):
            raise ValueError("Standing counters must be nonnegative integers")
        if self.played != self.won + self.drawn + self.lost or self.played > 18:
            raise ValueError("Standing games do not reconcile")
        if type(self.tie_break_order) is not int or not 1 <= self.tie_break_order <= 10:
            raise ValueError("Invalid preseason draw")

    @property
    def points(self) -> int:
        return self.won * 3 + self.drawn

    @property
    def goal_difference(self) -> int:
        return self.goals_for - self.goals_against


def add_result(standing: LeagueStanding, scored: int, conceded: int) -> LeagueStanding:
    if any(type(v) is not int or v < 0 for v in (scored, conceded)):
        raise ValueError("Invalid score")
    return replace(
        standing,
        played=standing.played + 1,
        won=standing.won + (scored > conceded),
        drawn=standing.drawn + (scored == conceded),
        lost=standing.lost + (scored < conceded),
        goals_for=standing.goals_for + scored,
        goals_against=standing.goals_against + conceded,
    )


def rank_league(rows: tuple[LeagueStanding, ...]) -> tuple[LeagueStanding, ...]:
    if (
        len(rows) != 10
        or len({r.club_id for r in rows}) != 10
        or {r.tie_break_order for r in rows} != set(range(1, 11))
    ):
        raise ValueError("League ranking needs ten clubs and a unique preseason draw")
    return tuple(
        sorted(rows, key=lambda r: (-r.points, -r.goal_difference, -r.goals_for, r.tie_break_order))
    )
