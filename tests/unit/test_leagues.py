from collections import Counter

import pytest

from goaletto.domain.leagues import plan_season_movements


def standings() -> dict[int, list[str]]:
    return {tier: [f"{tier}-{position}" for position in range(1, 11)] for tier in range(1, 6)}


def test_movements_preserve_fifty_clubs_and_ten_per_tier() -> None:
    final = standings()
    result = plan_season_movements(final)
    assert Counter(result.values()) == {tier: 10 for tier in range(1, 6)}
    assert set(result) == {club for clubs in final.values() for club in clubs}
    assert final == standings()  # Simultaneous plan must not mutate current memberships.


def test_top_and_bottom_boundaries_and_middle_movements() -> None:
    result = plan_season_movements(standings())
    assert result["1-1"] == 1
    assert result["1-9"] == 2
    assert result["2-1"] == 1
    assert result["3-2"] == 2
    assert result["3-8"] == 3
    assert result["3-10"] == 4
    assert result["5-1"] == 4
    assert result["5-10"] == 5


def test_duplicate_club_cannot_appear_in_two_tiers() -> None:
    final = standings()
    final[5][0] = final[1][0]
    with pytest.raises(ValueError, match="unique"):
        plan_season_movements(final)


def test_missing_tier_and_wrong_size_are_rejected() -> None:
    final = standings()
    final.pop(5)
    with pytest.raises(ValueError):
        plan_season_movements(final)
    final = standings()
    final[1].pop()
    with pytest.raises(ValueError):
        plan_season_movements(final)
