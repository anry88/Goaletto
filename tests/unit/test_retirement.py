import pytest

from goaletto.domain.retirement import PROBABILITIES_BPS, player_last_season


@pytest.mark.parametrize(
    "age,probability", list(zip(range(32, 39), PROBABILITIES_BPS, strict=True))
)
def test_progressive_draw_boundaries(age: int, probability: int) -> None:
    assert player_last_season(age, probability - 1).is_last_season
    if probability < 10000:
        assert not player_last_season(age, probability).is_last_season
    else:
        assert player_last_season(age, 9999).is_last_season
        assert player_last_season(age, 9999).draw is None


def test_young_players_never_announce_a_last_season() -> None:
    for age in range(17, 32):
        decision = player_last_season(age, 0)
        assert not decision.is_last_season and decision.draw is None
        assert decision.probability_bps == 0


@pytest.mark.parametrize(
    "age,draw", [(16, 0), (39, 0), (38, -1), (32, 10000), (True, 0), (32, True)]
)
def test_invalid_draw_or_active_age(age: int, draw: int) -> None:
    with pytest.raises(ValueError):
        player_last_season(age, draw)
