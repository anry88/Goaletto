"""Pure audience forecasts, never fabricated match/production measurements."""

import json
from collections import deque

from goaletto.domain.finances import stadium_upkeep, ticket_income
from goaletto.domain.stadium import match_attendance
from goaletto.domain.supporters import match_gain, season_gain


def simulate(wins: int, draws: int, place: int, tier: int, capacity: int) -> dict[str, int]:
    fans, tickets, full = 2500, 0, 0
    history: deque[int] = deque(maxlen=5)
    results = [3] * wins + [1] * draws + [0] * (18 - wins - draws)
    # Interleave the explicit results; no random schedules or real-world claims.
    results = results[::2] + results[1::2]
    for index, points in enumerate(results):
        if index % 2 == 0:
            crowd = match_attendance(
                fans, capacity, tier, place, tuple(history), competition="league"
            )
            tickets += ticket_income(crowd.fans, capacity)
            full += crowd.fans == capacity
        own, other = (2, 0) if points == 3 else (1, 1) if points == 1 else (0, 2)
        fans += match_gain(fans, tier, tier, "league", "league", own, other)
        history.append(points)
    next_tier = (
        tier - 1 if place <= 2 and tier > 1 else tier + 1 if place >= 9 and tier < 5 else tier
    )
    fans += season_gain(fans, tier, place, next_tier)
    assert 2500 <= fans < 20000 and full <= 9
    return {
        "supporters": fans,
        "tickets": tickets,
        "sold_out_home_games": full,
        "stadium_upkeep_80_days": 20 * stadium_upkeep(capacity),
    }


def main() -> None:
    rows = []
    for tier in range(1, 6):
        for name, wins, draws, place in [
            ("champion", 18, 0, 1),
            ("middle", 8, 5, 5),
            ("struggling", 2, 3, 10),
        ]:
            small = simulate(wins, draws, place, tier, 5000)
            large = simulate(wins, draws, place, tier, 10000)
            assert large["supporters"] == small["supporters"]
            assert large["tickets"] >= small["tickets"]
            rows.append(
                {"tier": tier, "scenario": name, "capacity_5000": small, "capacity_10000": large}
            )
    print(
        json.dumps(
            {
                "policy": "supporters-v1/stadium-demand-v2",
                "kind": "pure forecast; 18 explicit league results per scenario, "
                "no cup or real game data",
                "scenarios": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
