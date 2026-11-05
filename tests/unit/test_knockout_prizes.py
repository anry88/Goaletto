import pytest

from goaletto.domain.champions_bracket import ALLOWED_PARTICIPANT_COUNTS, rounds_for
from goaletto.domain.knockout_prizes import (
    CHAMPIONS_WINNER_PRIZE,
    CUP_EXIT_PRIZES,
    CUP_WINNER_PRIZE,
    champions_exit_prize,
)


@pytest.mark.parametrize("count", sorted(ALLOWED_PARTICIPANT_COUNTS))
def test_champions_prize_increases_at_every_stage_in_each_bracket(count):
    rounds = rounds_for(count)
    amounts = [champions_exit_prize(count, r) for r in range(1, rounds + 1)]
    assert amounts == sorted(set(amounts))
    assert amounts[0] == 12_000
    assert amounts[-1] == 280_000 < CHAMPIONS_WINNER_PRIZE
    with pytest.raises(ValueError, match="Invalid champions prize round"):
        champions_exit_prize(count, rounds + 1)


def test_fixed_cup_exit_scale_and_champions_round_two_by_bracket():
    assert list(CUP_EXIT_PRIZES.values()) == sorted(set(CUP_EXIT_PRIZES.values()))
    assert CUP_EXIT_PRIZES["prelim"] == 12_000
    assert CUP_EXIT_PRIZES["final"] < CUP_WINNER_PRIZE
    assert champions_exit_prize(8, 2) == 79_000
    assert champions_exit_prize(16, 2) == 41_000
