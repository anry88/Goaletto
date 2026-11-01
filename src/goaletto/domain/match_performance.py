"""One deterministic player-of-match award from frozen V5 performance counters."""

from uuid import UUID

from goaletto.domain.clock import MatchPhase
from goaletto.domain.match_types import POSITIONAL_ENGINES, MatchState


def player_of_match(state: MatchState) -> UUID | None:
    if state.engine_version not in POSITIONAL_ENGINES or state.phase != MatchPhase.FULL_TIME:
        return None
    candidates: list[tuple[int, int, int, int, int, UUID]] = []
    for team, opponent in ((state.home, state.away), (state.away, state.home)):
        for condition in team.conditions:
            if not condition.minutes_played:
                continue
            defensive = any(
                p in {"GK", "DC", "DL", "DR", "DM"}
                for p, minutes in condition.position_minutes
                if minutes >= 45
            )
            score = (
                condition.goals * 500
                + condition.assists * 350
                + condition.saves * 80
                + condition.minutes_played
                - condition.yellows * 80
                - int(condition.dismissed) * 350
                + (200 if defensive and opponent.stats.goals == 0 else 0)
                + (50 if team.stats.goals > opponent.stats.goals else 0)
            )
            candidates.append(
                (
                    score,
                    condition.goals,
                    condition.assists,
                    condition.saves,
                    -condition.player_id.int,
                    condition.player_id,
                )
            )
    return max(candidates)[-1] if candidates else None
