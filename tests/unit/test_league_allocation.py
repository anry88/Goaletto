"""Unit checks for clean cross-set promotion/relegation allocation."""

from collections import Counter
from random import Random
from uuid import UUID, uuid5

import pytest

from goaletto.domain.league_allocation import (
    ALLOCATION_POLICY,
    AllocationResult,
    LeagueSnapshot,
    allocate,
)

NAMESPACE = UUID("6f2f0f3e-2c3a-4b5e-9f1a-7c8d9e0f1a2b")


def _set_id(index: int) -> UUID:
    return uuid5(NAMESPACE, f"set:{index}")


def make_cohort(num_sets: int, cohort_key: str = "cohort-2026") -> list[LeagueSnapshot]:
    snapshots: list[LeagueSnapshot] = []
    for set_index in range(num_sets):
        set_id = _set_id(set_index)
        season_id = uuid5(set_id, "season:completed")
        for tier in range(1, 6):
            league_id = uuid5(set_id, f"league:{tier}")
            order = tuple(uuid5(league_id, f"club:{place}") for place in range(1, 11))
            snapshots.append(
                LeagueSnapshot(
                    cohort_key=cohort_key,
                    season_id=season_id,
                    league_id=league_id,
                    league_set_id=set_id,
                    tier=tier,
                    order=order,
                )
            )
    return snapshots


def _league_tier(snapshots: list[LeagueSnapshot]) -> dict[UUID, int]:
    return {snapshot.league_id: snapshot.tier for snapshot in snapshots}


def _check_invariants(snapshots: list[LeagueSnapshot], result: AllocationResult, seed: int) -> None:
    assert result.policy_version == ALLOCATION_POLICY
    assert result.seed == seed
    assert result.cohort_key == snapshots[0].cohort_key
    assert len(result.draws) == 5
    assert [draw.tier for draw in result.draws] == [1, 2, 3, 4, 5]

    input_clubs = [club for snapshot in snapshots for club in snapshot.order]
    assert len(input_clubs) == 10 * len(snapshots)
    assert {a.club_id for a in result.assignments} == set(input_clubs)
    assert len(result.assignments) == len(input_clubs)

    tier_of_league = _league_tier(snapshots)
    dest_counts = Counter(a.destination_league_id for a in result.assignments)
    assert set(dest_counts) == set(tier_of_league)
    assert all(count == 10 for count in dest_counts.values())

    dest_tier_counts = Counter(a.destination_tier for a in result.assignments)
    sets_count = len(snapshots) // 5
    assert dest_tier_counts == {tier: 10 * sets_count for tier in range(1, 6)}

    for assignment in result.assignments:
        assert tier_of_league[assignment.destination_league_id] == assignment.destination_tier
        if assignment.movement == "stayed":
            assert assignment.destination_league_id == assignment.source_league_id
            assert assignment.destination_tier == assignment.source_tier
        elif assignment.movement == "promoted":
            assert assignment.destination_tier == assignment.source_tier - 1
            assert assignment.source_tier > 1 and assignment.source_place <= 2
        else:
            assert assignment.movement == "relegated"
            assert assignment.destination_tier == assignment.source_tier + 1
            assert assignment.source_tier < 5 and assignment.source_place >= 9

    # Stayers keep their exact league.
    for snapshot in snapshots:
        for place, club_id in enumerate(snapshot.order, start=1):
            is_mover = (snapshot.tier > 1 and place <= 2) or (snapshot.tier < 5 and place >= 9)
            assignment = next(a for a in result.assignments if a.club_id == club_id)
            if is_mover:
                assert assignment.destination_league_id != snapshot.league_id
            else:
                assert assignment.destination_league_id == snapshot.league_id

    # Draws reconstruct mover placement: contiguous pairs per destination.
    by_club = {a.club_id: a for a in result.assignments}
    for draw in result.draws:
        assert len(draw.dest_league_ids) == sets_count
        if draw.tier > 1:
            assert len(draw.from_above) == 2 * sets_count
        else:
            assert draw.from_above == ()
        if draw.tier < 5:
            assert len(draw.from_below) == 2 * sets_count
        else:
            assert draw.from_below == ()
        for index, dest_league in enumerate(draw.dest_league_ids):
            expected: list[UUID] = []
            if draw.from_above:
                expected.extend(draw.from_above[index * 2 : (index + 1) * 2])
            if draw.from_below:
                expected.extend(draw.from_below[index * 2 : (index + 1) * 2])
            for club_id in expected:
                assert by_club[club_id].destination_league_id == dest_league


@pytest.mark.parametrize("num_sets", [1, 2, 3, 4])
def test_cohort_sizes_keep_ten_per_league(num_sets: int) -> None:
    snapshots = make_cohort(num_sets)
    result = allocate(snapshots, seed=7)
    _check_invariants(snapshots, result, 7)


@pytest.mark.parametrize("seed", [0, 1, 42, 2**62])
def test_different_seeds_stay_valid_and_deterministic(seed: int) -> None:
    snapshots = make_cohort(2)
    first = allocate(snapshots, seed=seed)
    second = allocate(snapshots, seed=seed)
    assert first == second
    _check_invariants(snapshots, first, seed)


def test_input_order_does_not_change_result() -> None:
    snapshots = make_cohort(3)
    base = allocate(snapshots, seed=11)
    reversed_result = allocate(list(reversed(snapshots)), seed=11)
    shuffled = list(snapshots)
    Random(3).shuffle(shuffled)
    shuffled_result = allocate(shuffled, seed=11)
    assert base == reversed_result == shuffled_result
    # Snapshots themselves are untouched.
    assert len(snapshots[0].order) == 10


def test_cross_set_moves_are_possible_without_flaky_single_seed() -> None:
    snapshots = make_cohort(2)
    set_of_league = {s.league_id: s.league_set_id for s in snapshots}
    source_set = {club: set_of_league[s.league_id] for s in snapshots for club in s.order}

    found_cross_set = False
    found_split_source = False
    for seed in range(30):
        result = allocate(snapshots, seed=seed)
        for assignment in result.assignments:
            if assignment.movement != "stayed" and (
                set_of_league[assignment.destination_league_id] != source_set[assignment.club_id]
            ):
                found_cross_set = True
        # Two promoted clubs from the same source league land apart for some seed.
        by_source: dict[UUID, list[UUID]] = {}
        for assignment in result.assignments:
            if assignment.movement != "stayed":
                by_source.setdefault(assignment.source_league_id, []).append(
                    assignment.destination_league_id
                )
        if any(len(set(dests)) > 1 for dests in by_source.values()):
            found_split_source = True
        if found_cross_set and found_split_source:
            break
    assert found_cross_set
    assert found_split_source


def test_single_set_movers_fill_only_own_tier_league() -> None:
    snapshots = make_cohort(1)
    result = allocate(snapshots, seed=5)
    _check_invariants(snapshots, result, 5)
    # With one league per tier there is exactly one destination option.
    tier_of_league = _league_tier(snapshots)
    for assignment in result.assignments:
        if assignment.movement != "stayed":
            assert tier_of_league[assignment.destination_league_id] == assignment.destination_tier


def test_empty_and_bad_seed_rejected() -> None:
    with pytest.raises(ValueError):
        allocate([], seed=0)
    snapshots = make_cohort(1)
    for bad in (-1, True, False, 2**63, "7"):
        with pytest.raises(ValueError):
            allocate(snapshots, seed=bad)  # type: ignore[arg-type]


def test_mixed_cohort_keys_rejected() -> None:
    snapshots = make_cohort(1)
    other = make_cohort(1, cohort_key="other-cohort")[0]
    with pytest.raises(ValueError, match="cohort"):
        allocate([*snapshots[:4], other], seed=0)


def test_duplicated_league_snapshot_rejected() -> None:
    snapshots = make_cohort(1)
    with pytest.raises(ValueError, match="[Dd]uplicat"):
        allocate([*snapshots, snapshots[0]], seed=0)


def test_duplicated_club_rejected() -> None:
    snapshots = make_cohort(1)
    first, second = snapshots[0], snapshots[1]
    tampered = LeagueSnapshot(
        cohort_key=second.cohort_key,
        season_id=second.season_id,
        league_id=second.league_id,
        league_set_id=second.league_set_id,
        tier=second.tier,
        order=(*second.order[:9], first.order[0]),
    )
    with pytest.raises(ValueError, match="unique"):
        allocate([first, tampered, *snapshots[2:]], seed=0)


def test_incomplete_vertical_rejected() -> None:
    snapshots = make_cohort(2)
    with pytest.raises(ValueError, match="[Vv]ertical|tier"):
        allocate(snapshots[:-1], seed=0)


def test_unbalanced_tiers_rejected() -> None:
    snapshots = make_cohort(1)
    extra = LeagueSnapshot(
        cohort_key="cohort-2026",
        season_id=uuid5(NAMESPACE, "extra-season"),
        league_id=uuid5(NAMESPACE, "extra-league"),
        league_set_id=uuid5(NAMESPACE, "extra-set"),
        tier=1,
        order=tuple(uuid5(NAMESPACE, f"extra:{i}") for i in range(10)),
    )
    with pytest.raises(ValueError):
        allocate([*snapshots, extra], seed=0)


def test_incomplete_set_vertical_rejected() -> None:
    # Two sets where the second one lacks tier 5: per-tier counts differ
    # and the second set vertical is incomplete.
    full = make_cohort(1)
    partial = make_cohort(2)[5:9]  # second set, tiers 1..4 only
    with pytest.raises(ValueError):
        allocate([*full, *partial], seed=0)


def test_short_order_snapshot_rejected() -> None:
    snapshots = make_cohort(1)
    target = snapshots[2]
    with pytest.raises(ValueError):
        LeagueSnapshot(
            cohort_key=target.cohort_key,
            season_id=target.season_id,
            league_id=target.league_id,
            league_set_id=target.league_set_id,
            tier=target.tier,
            order=target.order[:9],
        )


def test_result_helpers_and_immutability() -> None:
    snapshots = make_cohort(2)
    result = allocate(snapshots, seed=9)
    sample = result.assignments[0].club_id
    assert result.destination_for(sample) in {s.league_id for s in snapshots}
    with pytest.raises(KeyError):
        result.destination_for(uuid5(NAMESPACE, "missing-club"))
    grouped = result.by_league()
    assert all(len(clubs) == 10 for clubs in grouped.values())
    with pytest.raises(AttributeError):
        result.assignments.append(result.assignments[0])  # type: ignore[attr-defined]
