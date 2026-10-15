"""Unit checks for the pure neutral-final ticket split (AND-125 subpackage)."""

import dataclasses
from uuid import UUID, uuid4

import pytest

from goaletto.domain.finances import MAX_MONEY
from goaletto.domain.neutral_final import (
    HOME_ADVANTAGE,
    NEUTRAL_DEMAND_BPS,
    NEUTRAL_FINAL_POLICY,
    NeutralFinalResult,
    NeutralFinalSnapshot,
    settle_neutral_final,
)

VENUE = UUID("33333333-3333-3333-3333-333333333333")
CLUB_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
CLUB_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def make_snapshot(
    *,
    venue_id: UUID = VENUE,
    capacity: int = 50_000,
    ticket_price: int = 5,
    club_a_id: UUID = CLUB_A,
    club_b_id: UUID = CLUB_B,
    supporters_a: int = 2_500,
    supporters_b: int = 2_500,
    seed: int = 7,
    neutral: bool = True,
) -> NeutralFinalSnapshot:
    return NeutralFinalSnapshot(
        policy_version=NEUTRAL_FINAL_POLICY,
        venue_id=venue_id,
        capacity=capacity,
        ticket_price=ticket_price,
        club_a_id=club_a_id,
        club_b_id=club_b_id,
        supporters_a=supporters_a,
        supporters_b=supporters_b,
        seed=seed,
        neutral=neutral,
    )


def test_zero_capacity_gives_empty_house() -> None:
    result = settle_neutral_final(make_snapshot(capacity=0))
    assert result.crowd == 0
    assert result.gross == 0
    assert (result.payout_a, result.payout_b) == (0, 0)
    assert result.odd_recipient is None
    assert result.neutral is True
    assert result.home_advantage == 0 and type(result.home_advantage) is int


def test_full_and_limited_capacity() -> None:
    # Demand uncapped: (2500+2500)*8000//10000 = 4000.
    full = settle_neutral_final(make_snapshot(capacity=50_000))
    assert full.crowd == 4_000
    assert full.gross == 20_000
    # Limited venue caps the same demand.
    limited = settle_neutral_final(make_snapshot(capacity=1_000))
    assert limited.crowd == 1_000
    assert limited.gross == 5_000
    # Huge fanbases still capped by the venue.
    capped = settle_neutral_final(
        make_snapshot(capacity=5_000, supporters_a=100_000, supporters_b=100_000)
    )
    assert capped.crowd == 5_000
    assert capped.gross == 25_000


def test_both_fanbases_contribute() -> None:
    base = settle_neutral_final(make_snapshot(capacity=50_000))
    assert base.crowd == 4_000
    boosted_a = settle_neutral_final(
        make_snapshot(capacity=50_000, supporters_a=5_000, supporters_b=2_500)
    )
    boosted_b = settle_neutral_final(
        make_snapshot(capacity=50_000, supporters_a=2_500, supporters_b=5_000)
    )
    assert boosted_a.crowd > base.crowd
    assert boosted_b.crowd > base.crowd
    assert boosted_a.crowd == boosted_b.crowd  # symmetric influence
    solo = settle_neutral_final(make_snapshot(capacity=50_000, supporters_a=2_500, supporters_b=0))
    assert solo.crowd == 2_500 * NEUTRAL_DEMAND_BPS // 10_000 == 2_000
    empty = settle_neutral_final(make_snapshot(capacity=50_000, supporters_a=0, supporters_b=0))
    assert empty.crowd == 0 and empty.gross == 0


def test_even_gross_splits_equally_without_recipient() -> None:
    result = settle_neutral_final(make_snapshot(capacity=50_000))  # gross 20000
    assert result.gross % 2 == 0
    assert result.payout_a == result.payout_b == result.gross // 2
    assert result.odd_recipient is None
    assert result.payout_a + result.payout_b == result.gross


def test_odd_gross_gives_single_extra_unit_to_one_finalist() -> None:
    # total supporters 2 -> crowd 1; ticket 1 -> gross 1 (odd).
    result = settle_neutral_final(
        make_snapshot(capacity=50_000, supporters_a=1, supporters_b=1, ticket_price=1, seed=7)
    )
    assert result.crowd == 1
    assert result.gross == 1
    assert sorted((result.payout_a, result.payout_b)) == [0, 1]
    assert result.payout_a + result.payout_b == 1
    assert abs(result.payout_a - result.payout_b) == 1
    assert result.odd_recipient in (CLUB_A, CLUB_B)
    # The recipient is the club holding the extra unit.
    if result.odd_recipient == CLUB_A:
        assert result.payout_a == 1 and result.payout_b == 0
    else:
        assert result.payout_a == 0 and result.payout_b == 1


def test_small_ticket_price() -> None:
    one = settle_neutral_final(
        make_snapshot(capacity=50_000, supporters_a=2_500, supporters_b=2_500, ticket_price=1)
    )
    assert one.crowd == 4_000
    assert one.gross == 4_000
    assert one.payout_a == one.payout_b == 2_000
    free = settle_neutral_final(
        make_snapshot(capacity=50_000, supporters_a=2_500, supporters_b=2_500, ticket_price=0)
    )
    assert free.gross == 0
    assert (free.payout_a, free.payout_b) == (0, 0)
    assert free.odd_recipient is None


def test_seed_stable_and_swap_preserves_club_recipient() -> None:
    first = settle_neutral_final(
        make_snapshot(capacity=50_000, supporters_a=1, supporters_b=1, ticket_price=1, seed=7)
    )
    second = settle_neutral_final(
        make_snapshot(capacity=50_000, supporters_a=1, supporters_b=1, ticket_price=1, seed=7)
    )
    assert first == second
    # Swapping A/B keeps crowd/gross, swaps payouts, keeps the same club recipient.
    swapped = settle_neutral_final(
        make_snapshot(
            capacity=50_000,
            club_a_id=CLUB_B,
            club_b_id=CLUB_A,
            supporters_a=1,
            supporters_b=1,
            ticket_price=1,
            seed=7,
        )
    )
    assert swapped.crowd == first.crowd and swapped.gross == first.gross
    assert swapped.payout_a == first.payout_b and swapped.payout_b == first.payout_a
    assert swapped.odd_recipient == first.odd_recipient
    # Each seed replays deterministically and always yields a valid split.
    seen: set[UUID | None] = set()
    for seed in (0, 1, 42, 2**62):
        res = settle_neutral_final(
            make_snapshot(
                capacity=50_000, supporters_a=1, supporters_b=1, ticket_price=1, seed=seed
            )
        )
        assert res == settle_neutral_final(
            make_snapshot(
                capacity=50_000, supporters_a=1, supporters_b=1, ticket_price=1, seed=seed
            )
        )
        assert res.payout_a + res.payout_b == res.gross
        seen.add(res.odd_recipient)
    assert seen <= {CLUB_A, CLUB_B}


def test_no_home_advantage_and_explicit_neutral_flag() -> None:
    result = settle_neutral_final(make_snapshot())
    assert HOME_ADVANTAGE == 0
    assert result.home_advantage == 0 and type(result.home_advantage) is int
    assert result.neutral is True
    # Neutrality is not a slot permutation: swapping clubs keeps advantage zero.
    swapped = settle_neutral_final(
        make_snapshot(club_a_id=CLUB_B, club_b_id=CLUB_A, supporters_a=100, supporters_b=900)
    )
    straight = settle_neutral_final(
        make_snapshot(club_a_id=CLUB_A, club_b_id=CLUB_B, supporters_a=900, supporters_b=100)
    )
    assert swapped.home_advantage == straight.home_advantage == 0
    assert swapped.neutral is True and straight.neutral is True


def test_large_numbers_fit_and_overflow_rejected() -> None:
    big = settle_neutral_final(
        make_snapshot(
            capacity=MAX_MONEY,
            supporters_a=MAX_MONEY,
            supporters_b=MAX_MONEY,
            ticket_price=0,
            seed=0,
        )
    )
    assert big.crowd == MAX_MONEY  # capped by venue, integer math, no float
    assert big.gross == 0
    # Snapshot with max inputs is valid; the overflow surfaces at settlement.
    overflow = make_snapshot(
        capacity=MAX_MONEY,
        supporters_a=MAX_MONEY,
        supporters_b=MAX_MONEY,
        ticket_price=MAX_MONEY,
        seed=0,
    )
    with pytest.raises(ValueError):
        settle_neutral_final(overflow)


def test_invalid_inputs_rejected() -> None:
    with pytest.raises(ValueError):
        make_snapshot(club_a_id=CLUB_A, club_b_id=CLUB_A)
    for bad in (-1, -100):
        with pytest.raises(ValueError):
            make_snapshot(capacity=bad)
        with pytest.raises(ValueError):
            make_snapshot(ticket_price=bad)
        with pytest.raises(ValueError):
            make_snapshot(supporters_a=bad)
        with pytest.raises(ValueError):
            make_snapshot(supporters_b=bad)
    # bool is not int.
    for kwargs in (
        {"capacity": True},
        {"ticket_price": False},
        {"supporters_a": True},
        {"supporters_b": False},
        {"seed": True},
    ):
        with pytest.raises(ValueError):
            make_snapshot(**kwargs)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        make_snapshot(seed=-1)
    with pytest.raises(ValueError):
        make_snapshot(seed=2**63)
    with pytest.raises(ValueError):
        NeutralFinalSnapshot(
            policy_version="stadium-demand-v1",
            venue_id=VENUE,
            capacity=1_000,
            ticket_price=5,
            club_a_id=CLUB_A,
            club_b_id=CLUB_B,
            supporters_a=10,
            supporters_b=10,
            seed=0,
            neutral=True,
        )
    with pytest.raises(ValueError):
        make_snapshot(neutral=False)
    with pytest.raises(ValueError):
        make_snapshot(neutral=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        make_snapshot(venue_id="not-a-uuid")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        make_snapshot(club_a_id="not-a-uuid")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        settle_neutral_final("not-a-snapshot")  # type: ignore[arg-type]


def test_results_immutable_integer_only_and_sum_to_gross() -> None:
    result = settle_neutral_final(make_snapshot())
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.payout_a = 0  # type: ignore[misc]
    snapshot = make_snapshot()
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.capacity = 0  # type: ignore[misc]
    for value in (
        result.crowd,
        result.gross,
        result.payout_a,
        result.payout_b,
        result.capacity,
        result.ticket_price,
    ):
        assert type(value) is int
        assert isinstance(value, int) and not isinstance(value, bool)
    assert result.payout_a + result.payout_b == result.gross
    assert abs(result.payout_a - result.payout_b) <= 1
    assert isinstance(result.odd_recipient, UUID | type(None))
    # No third payout: only the two finalist amounts exist on the result.
    assert not hasattr(result, "payout_venue")
    assert not hasattr(result, "venue_payout")


def test_venue_identity_carries_no_payout_even_on_finalist_ground() -> None:
    on_a_ground = settle_neutral_final(make_snapshot(venue_id=CLUB_A))
    assert on_a_ground.neutral is True
    assert on_a_ground.home_advantage == 0
    assert on_a_ground.payout_a + on_a_ground.payout_b == on_a_ground.gross
    assert isinstance(on_a_ground, NeutralFinalResult)
    fresh_venue = settle_neutral_final(make_snapshot(venue_id=uuid4()))
    assert fresh_venue.payout_a + fresh_venue.payout_b == fresh_venue.gross


@pytest.mark.parametrize("damage", ["recipient", "amounts", "both", "crowd"])
def test_restored_result_rejects_contradictory_demand_or_remainder(damage):
    result = settle_neutral_final(make_snapshot(supporters_a=1, supporters_b=1, ticket_price=1))
    other = CLUB_B if result.odd_recipient == CLUB_A else CLUB_A
    fields = {}
    if damage in ("recipient", "both"):
        fields["odd_recipient"] = other
    if damage in ("amounts", "both"):
        fields.update(payout_a=result.payout_b, payout_b=result.payout_a)
    if damage == "crowd":
        result = settle_neutral_final(make_snapshot(supporters_a=0, supporters_b=0, ticket_price=2))
        fields.update(crowd=1, gross=2, payout_a=1, payout_b=1)
    with pytest.raises(ValueError):
        dataclasses.replace(result, **fields)
