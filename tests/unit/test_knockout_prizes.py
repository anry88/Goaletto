from uuid import uuid4

import pytest

from goaletto.application.knockout_prizes import Prize, _matches_paid_prize
from goaletto.application.money import request
from goaletto.domain.champions_bracket import ALLOWED_PARTICIPANT_COUNTS, rounds_for
from goaletto.domain.knockout_prizes import (
    CHAMPIONS_REBALANCED_POLICY,
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
    assert amounts[0] == 3_000
    assert amounts[-1] == 68_000 < CHAMPIONS_WINNER_PRIZE
    with pytest.raises(ValueError, match="Invalid champions prize round"):
        champions_exit_prize(count, rounds + 1)


def test_fixed_cup_exit_scale_and_champions_round_two_by_bracket():
    assert list(CUP_EXIT_PRIZES.values()) == sorted(set(CUP_EXIT_PRIZES.values()))
    assert CUP_EXIT_PRIZES["prelim"] == 12_000
    assert CUP_EXIT_PRIZES["final"] < CUP_WINNER_PRIZE
    assert champions_exit_prize(8, 2) == 19_000
    assert champions_exit_prize(16, 2) == 10_000


def test_champions_qualifying_tier_multipliers():
    assert [champions_prize_multiplier(tier) for tier in range(1, 6)] == [5, 4, 3, 2, 1]
    assert CHAMPIONS_WINNER_PRIZE * champions_prize_multiplier(1) == 550_000
    with pytest.raises(ValueError, match="Invalid champions qualifying tier"):
        champions_prize_multiplier(6)


@pytest.mark.parametrize("policy, factor", [(POLICY, 1), (CHAMPIONS_TIER_POLICY, 5)])
@pytest.mark.parametrize(
    "placement, round_number, legacy_base, current_base",
    [("eliminated", 2, 79_000, 19_000), ("champion", 3, 450_000, 110_000)],
)
def test_old_champions_receipt_still_settles_the_same_placement(
    policy, factor, placement, round_number, legacy_base, current_base
):
    original = {
        "policy": policy,
        "competition": "champions",
        "tie_id": str(uuid4()),
        "participants": 8,
        "round": round_number,
        "placement": placement,
    }
    details = dict(
        original, policy=CHAMPIONS_REBALANCED_POLICY, qualifying_tier=1, tier_multiplier=5
    )
    if policy == CHAMPIONS_TIER_POLICY:
        original.update(qualifying_tier=1, tier_multiplier=5)
    amount = legacy_base * factor
    entry = LedgerEntry(kind="prize", cash_delta=amount, request=request("prize", amount, original))
    expected = Prize(uuid4(), "champions:prize:edition", current_base * 5, details)
    assert _matches_paid_prize(entry, expected)
    entry.request = request("prize", amount, dict(original, tie_id=str(uuid4())))
    assert not _matches_paid_prize(entry, expected)
