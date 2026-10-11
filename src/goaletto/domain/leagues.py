"""Simultaneous season movement plan for one independent five-tier league set."""

from collections.abc import Mapping, Sequence

LEAGUES_PER_SET = 5
CLUBS_PER_LEAGUE = 10
ENTRY_TIER = 5
MOVEMENT_PLACES = 2


def plan_season_movements(standings: Mapping[int, Sequence[str]]) -> dict[str, int]:
    """Return club -> next tier from FINAL sorted standings, without mutating input."""
    if set(standings) != set(range(1, LEAGUES_PER_SET + 1)):
        raise ValueError("A league set must have tiers 1 through 5")
    seen: set[str] = set()
    movements: dict[str, int] = {}
    for tier in range(1, LEAGUES_PER_SET + 1):
        clubs = standings[tier]
        if len(clubs) != CLUBS_PER_LEAGUE:
            raise ValueError("Every tier must have exactly ten clubs")
        for position, club_id in enumerate(clubs, start=1):
            if not club_id or club_id in seen:
                raise ValueError("All fifty club IDs must be nonempty and unique")
            seen.add(club_id)
            if position <= MOVEMENT_PLACES and tier > 1:
                target = tier - 1
            elif position > CLUBS_PER_LEAGUE - MOVEMENT_PLACES and tier < ENTRY_TIER:
                target = tier + 1
            else:
                target = tier
            movements[club_id] = target
    return movements
