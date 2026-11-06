from uuid import uuid4

import pytest

from goaletto.application.knockout_prizes import Prize, _matches_paid_prize
from goaletto.application.money import request
from goaletto.domain.champions_bracket import ALLOWED_PARTICIPANT_COUNTS, rounds_for
from goaletto.domain.knockout_prizes import (
    CHAMPIONS_TIER_POLICY,
    CHAMPIONS_WINNER_PRIZE,
    CUP_EXIT_PRIZES,
    CUP_WINNER_PRIZE,
    POLICY,
    champions_exit_prize,
    champions_prize_multiplier,
)
from goaletto.infrastructure.models import LedgerEntry


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


def test_champions_qualifying_tier_multipliers():
    assert [champions_prize_multiplier(tier) for tier in range(1, 6)] == [5, 4, 3, 2, 1]
    with pytest.raises(ValueError, match="Invalid champions qualifying tier"):
        champions_prize_multiplier(6)


def test_old_flat_champions_receipt_still_settles_the_same_placement():
    original = {"policy": POLICY, "competition": "champions", "tie_id": str(uuid4())}
    entry = LedgerEntry(kind="prize", cash_delta=12_000, request=request("prize", 12_000, original))
    details = dict(original, policy=CHAMPIONS_TIER_POLICY, qualifying_tier=1, tier_multiplier=5)
    expected = Prize(uuid4(), "champions:prize:edition", 60_000, details)
    assert _matches_paid_prize(entry, expected)
    entry.request = request("prize", 12_000, dict(original, tie_id=str(uuid4())))
    assert not _matches_paid_prize(entry, expected)
