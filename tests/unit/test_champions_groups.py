"""Unit checks for clean champions-cup qualification and grouping (AND-126)."""

from random import Random
from uuid import UUID, uuid5

import pytest

from goaletto.domain.champions_groups import (
    ALLOWED_GROUP_SIZES,
    CHAMPIONS_GROUPS_POLICY,
    CHAMPIONS_PHASE_DAY,
    TOTAL_CHAMPIONS_CYCLES,
    ChampionsGroupsResult,
    build_champions_groups,
    champions_cycles_for,
    free_cycles_for,
    group_id_for,
    match_days_for,
    participant_id_for,
    rounds_for,
    split_group_sizes,
)
from goaletto.domain.league_allocation import LeagueSnapshot

NAMESPACE = UUID("6f2f0f3e-2c3a-4b5e-9f1a-7c8d9e0f1a2b")


def _set_id(index: int) -> UUID:
    return uuid5(NAMESPACE, f"champions-set:{index}")


def make_cohort(num_sets: int, cohort_key: str = "champions-cohort-2026") -> list[LeagueSnapshot]:
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


def _check_result_invariants(
    snapshots: list[LeagueSnapshot], result: ChampionsGroupsResult, seed: int
) -> None:
    assert result.policy_version == CHAMPIONS_GROUPS_POLICY
    assert result.seed == seed
    assert result.cohort_key == snapshots[0].cohort_key
    assert len(result.draws) == 5
    assert sorted(draw.tier for draw in result.draws) == [1, 2, 3, 4, 5]

    num_sets = len(snapshots) // 5
    expected_sizes = split_group_sizes(num_sets)
    by_tier_snaps: dict[int, list[LeagueSnapshot]] = {tier: [] for tier in range(1, 6)}
    for snapshot in snapshots:
        by_tier_snaps[snapshot.tier].append(snapshot)

    for draw in result.draws:
        assert draw.group_sizes == expected_sizes
        assert len(draw.ordered_set_ids) == num_sets
        assert len(draw.group_ids) == len(expected_sizes)
        assert len(set(draw.ordered_set_ids)) == num_sets

    for tier in range(1, 6):
        tier_groups = sorted(
            (group for group in result.groups if group.tier == tier),
            key=lambda group: group.group_index,
        )
        assert [len(group.league_set_ids) for group in tier_groups] == list(expected_sizes)
        # Every set exactly once per tier; every qualifier exactly once per tier.
        seen_sets: set[UUID] = set()
        seen_clubs: set[UUID] = set()
        for group in tier_groups:
            assert group.tier == tier
            assert group.phase_day == CHAMPIONS_PHASE_DAY
            assert group.participant_count == 8 * len(group.league_set_ids)
            assert group.participant_count in (8, 16, 32, 64, 128, 256, 512)
            assert group.rounds == rounds_for(group.participant_count)
            assert group.match_days == match_days_for(group.participant_count)
            assert group.champions_cycles == tuple(range(1, group.match_days + 1))
            free_expected = tuple(range(group.match_days + 1, TOTAL_CHAMPIONS_CYCLES + 1))
            assert group.free_cycles == free_expected
            assert group.free_cycles[-1] == TOTAL_CHAMPIONS_CYCLES
            assert group.id == group_id_for(result.cohort_key, tier, group.group_index)
            for set_id in group.league_set_ids:
                assert set_id not in seen_sets
                seen_sets.add(set_id)
            for participant in group.participants:
                assert participant.qualifying_tier == tier
                assert participant.qualifying_place in range(1, 9)
                assert participant.participant_id == participant_id_for(
                    result.cohort_key, tier, participant.club_id
                )
                assert participant.club_id not in seen_clubs
                seen_clubs.add(participant.club_id)
        assert len(seen_sets) == num_sets
        assert len(seen_clubs) == 8 * num_sets
        # Top eight kept, places 9..10 excluded, tiers never mix.
        expected_clubs: set[UUID] = set()
        excluded_clubs: set[UUID] = set()
        for snapshot in by_tier_snaps[tier]:
            expected_clubs.update(snapshot.order[:8])
            excluded_clubs.update(snapshot.order[8:])
            for place, club_id in enumerate(snapshot.order[:8], start=1):
                found = next(
                    p for group in tier_groups for p in group.participants if p.club_id == club_id
                )
                assert found.qualifying_place == place
                assert found.source_league_id == snapshot.league_id
                assert found.source_league_set_id == snapshot.league_set_id
                assert found.qualifying_season_id == snapshot.season_id
        assert seen_clubs == expected_clubs
        assert not (seen_clubs & excluded_clubs)


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        (1, (1,)),
        (2, (2,)),
        (3, (2, 1)),
        (4, (4,)),
        (5, (4, 1)),
        (6, (4, 2)),
        (7, (4, 2, 1)),
        (8, (8,)),
        (64, (64,)),
        (65, (64, 1)),
        (66, (64, 2)),
        (96, (64, 32)),
        (100, (64, 32, 4)),
        (129, (64, 64, 1)),
        (130, (64, 64, 2)),
    ],
)
def test_split_group_sizes_table(total: int, expected: tuple[int, ...]) -> None:
    assert split_group_sizes(total) == expected


@pytest.mark.parametrize(
    ("group_size", "participants", "rounds", "days"),
    [
        (1, 8, 3, 5),
        (2, 16, 4, 7),
        (4, 32, 5, 9),
        (8, 64, 6, 11),
        (16, 128, 7, 13),
        (32, 256, 8, 15),
        (64, 512, 9, 17),
    ],
)
def test_structural_days_table(group_size: int, participants: int, rounds: int, days: int) -> None:
    assert rounds_for(participants) == rounds
    assert match_days_for(participants) == days
    assert champions_cycles_for(group_size) == tuple(range(1, days + 1))
    free = free_cycles_for(group_size)
    assert free == tuple(range(days + 1, 19))
    assert free[-1] == 18
    assert group_size in ALLOWED_GROUP_SIZES


def test_single_set_one_group_per_tier() -> None:
    snapshots = make_cohort(1)
    result = build_champions_groups(snapshots, seed=7)
    _check_result_invariants(snapshots, result, 7)
    assert len(result.groups) == 5
    for group in result.groups:
        assert len(group.league_set_ids) == 1
        assert group.participant_count == 8
        assert group.rounds == 3
        assert group.match_days == 5
        assert group.champions_cycles == (1, 2, 3, 4, 5)
        assert group.free_cycles == tuple(range(6, 19))


def test_two_sets_single_group() -> None:
    snapshots = make_cohort(2)
    result = build_champions_groups(snapshots, seed=7)
    _check_result_invariants(snapshots, result, 7)
    for tier in range(1, 6):
        tier_groups = [group for group in result.groups if group.tier == tier]
        assert len(tier_groups) == 1
        assert tier_groups[0].participant_count == 16
        assert tier_groups[0].match_days == 7


def test_three_sets_split_two_plus_one() -> None:
    snapshots = make_cohort(3)
    result = build_champions_groups(snapshots, seed=7)
    _check_result_invariants(snapshots, result, 7)
    for tier in range(1, 6):
        tier_groups = sorted(
            (group for group in result.groups if group.tier == tier),
            key=lambda group: group.group_index,
        )
        assert [len(group.league_set_ids) for group in tier_groups] == [2, 1]
        assert [group.participant_count for group in tier_groups] == [16, 8]
        assert [group.match_days for group in tier_groups] == [7, 5]


def test_four_sets_single_group() -> None:
    snapshots = make_cohort(4)
    result = build_champions_groups(snapshots, seed=7)
    _check_result_invariants(snapshots, result, 7)
    for tier in range(1, 6):
        tier_groups = [group for group in result.groups if group.tier == tier]
        assert len(tier_groups) == 1
        assert tier_groups[0].participant_count == 32
        assert tier_groups[0].match_days == 9


def test_six_sets_split_four_plus_two() -> None:
    snapshots = make_cohort(6)
    result = build_champions_groups(snapshots, seed=11)
    _check_result_invariants(snapshots, result, 11)
    for tier in range(1, 6):
        tier_groups = sorted(
            (group for group in result.groups if group.tier == tier),
            key=lambda group: group.group_index,
        )
        assert [len(group.league_set_ids) for group in tier_groups] == [4, 2]


def test_seven_sets_split_four_two_one() -> None:
    snapshots = make_cohort(7)
    result = build_champions_groups(snapshots, seed=11)
    _check_result_invariants(snapshots, result, 11)
    for tier in range(1, 6):
        tier_groups = sorted(
            (group for group in result.groups if group.tier == tier),
            key=lambda group: group.group_index,
        )
        assert [len(group.league_set_ids) for group in tier_groups] == [4, 2, 1]
        assert [group.match_days for group in tier_groups] == [9, 7, 5]


def test_sixty_four_sets_single_large_group() -> None:
    snapshots = make_cohort(64)
    result = build_champions_groups(snapshots, seed=3)
    _check_result_invariants(snapshots, result, 3)
    for tier in range(1, 6):
        tier_groups = [group for group in result.groups if group.tier == tier]
        assert len(tier_groups) == 1
        group = tier_groups[0]
        assert len(group.league_set_ids) == 64
        assert group.participant_count == 512
        assert group.rounds == 9
        assert group.match_days == 17
        assert group.champions_cycles == tuple(range(1, 18))
        assert group.free_cycles == (18,)


def test_sixty_five_sets_sixty_four_plus_one() -> None:
    snapshots = make_cohort(65)
    result = build_champions_groups(snapshots, seed=3)
    _check_result_invariants(snapshots, result, 3)
    for tier in range(1, 6):
        tier_groups = sorted(
            (group for group in result.groups if group.tier == tier),
            key=lambda group: group.group_index,
        )
        assert [len(group.league_set_ids) for group in tier_groups] == [64, 1]
        assert tier_groups[0].match_days == 17
        assert tier_groups[0].free_cycles == (18,)
        assert tier_groups[1].match_days == 5


def test_large_cohort_over_128() -> None:
    snapshots = make_cohort(129)
    result = build_champions_groups(snapshots, seed=9)
    _check_result_invariants(snapshots, result, 9)
    for tier in range(1, 6):
        tier_groups = sorted(
            (group for group in result.groups if group.tier == tier),
            key=lambda group: group.group_index,
        )
        assert [len(group.league_set_ids) for group in tier_groups] == [64, 64, 1]
        total = sum(len(group.league_set_ids) for group in tier_groups)
        assert total == 129
        total_clubs = sum(group.participant_count for group in tier_groups)
        assert total_clubs == 8 * 129


def test_places_nine_ten_never_qualify() -> None:
    snapshots = make_cohort(4)
    result = build_champions_groups(snapshots, seed=5)
    for snapshot in snapshots:
        tier_groups = [group for group in result.groups if group.tier == snapshot.tier]
        tier_clubs = {p.club_id for group in tier_groups for p in group.participants}
        assert snapshot.order[8] not in tier_clubs
        assert snapshot.order[9] not in tier_clubs
        for club_id in snapshot.order[:8]:
            assert club_id in tier_clubs


def test_stable_ids_and_replay() -> None:
    snapshots = make_cohort(7)
    first = build_champions_groups(snapshots, seed=11)
    second = build_champions_groups(list(snapshots), seed=11)
    assert first == second
    _check_result_invariants(snapshots, first, 11)


def test_input_permutation_does_not_change_draw() -> None:
    snapshots = make_cohort(7)
    shuffled = list(snapshots)
    Random(0).shuffle(shuffled)
    assert shuffled != snapshots
    first = build_champions_groups(snapshots, seed=11)
    second = build_champions_groups(shuffled, seed=11)
    assert first == second


def test_different_seeds_change_group_composition() -> None:
    snapshots = make_cohort(7)
    first = build_champions_groups(snapshots, seed=1)
    second = build_champions_groups(snapshots, seed=2)
    _check_result_invariants(snapshots, first, 1)
    _check_result_invariants(snapshots, second, 2)
    # Same overall qualifiers per tier, same sizes, but a different draw.
    assert [draw.group_sizes for draw in first.draws] == [draw.group_sizes for draw in second.draws]
    assert any(
        first_draw.ordered_set_ids != second_draw.ordered_set_ids
        for first_draw, second_draw in zip(first.draws, second.draws, strict=True)
    )
    # Group IDs are seed-independent (index-based); membership varies with the seed.
    assert [group.id for group in first.groups] == [group.id for group in second.groups]
    assert first.groups != second.groups


def test_empty_cohort_is_no_qualification_not_invented_clubs() -> None:
    with pytest.raises(ValueError):
        build_champions_groups([], seed=1)


def test_mixed_cohort_keys_rejected() -> None:
    snapshots = make_cohort(1)
    other = make_cohort(1, cohort_key="other-cohort")
    with pytest.raises(ValueError):
        build_champions_groups([*snapshots, *other], seed=1)


def test_duplicated_league_rejected() -> None:
    snapshots = make_cohort(1)
    with pytest.raises(ValueError):
        build_champions_groups([*snapshots, snapshots[0]], seed=1)


def test_duplicated_club_rejected() -> None:
    snapshots = make_cohort(1)
    first, second = snapshots[0], snapshots[1]
    broken = LeagueSnapshot(
        cohort_key=first.cohort_key,
        season_id=second.season_id,
        league_id=second.league_id,
        league_set_id=second.league_set_id,
        tier=second.tier,
        order=first.order,
    )
    with pytest.raises(ValueError):
        build_champions_groups([first, broken, *snapshots[2:]], seed=1)


def test_incomplete_vertical_rejected() -> None:
    snapshots = make_cohort(1)
    with pytest.raises(ValueError):
        build_champions_groups([s for s in snapshots if s.tier != 5], seed=1)


def test_season_in_two_sets_rejected() -> None:
    snapshots = make_cohort(2)
    # Reuse the first set season inside the second set leagues.
    first_season = next(s.season_id for s in snapshots if s.league_set_id == _set_id(0))
    rebuilt: list[LeagueSnapshot] = []
    for snapshot in snapshots:
        if snapshot.league_set_id == _set_id(1):
            rebuilt.append(
                LeagueSnapshot(
                    cohort_key=snapshot.cohort_key,
                    season_id=first_season,
                    league_id=snapshot.league_id,
                    league_set_id=snapshot.league_set_id,
                    tier=snapshot.tier,
                    order=snapshot.order,
                )
            )
        else:
            rebuilt.append(snapshot)
    with pytest.raises(ValueError):
        build_champions_groups(rebuilt, seed=1)


@pytest.mark.parametrize("bad_seed", [True, False, -1, 2**63, "7", 1.0, None])
def test_invalid_seed_rejected(bad_seed: object) -> None:
    snapshots = make_cohort(1)
    with pytest.raises(ValueError):
        build_champions_groups(snapshots, seed=bad_seed)  # type: ignore[arg-type]


def test_invalid_helpers_rejected() -> None:
    with pytest.raises(ValueError):
        split_group_sizes(0)
    with pytest.raises(ValueError):
        split_group_sizes(True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        rounds_for(7)
    with pytest.raises(ValueError):
        match_days_for(24)
    with pytest.raises(ValueError):
        champions_cycles_for(3)
    with pytest.raises(ValueError):
        free_cycles_for(0)
    with pytest.raises(ValueError):
        group_id_for("", 1, 0)
    with pytest.raises(ValueError):
        participant_id_for("cohort", 6, _set_id(0))


def test_restored_draw_must_match_exact_group_partition():
    from dataclasses import replace

    result = build_champions_groups(make_cohort(3), seed=7)
    draw = result.draws[0]
    changed = (draw.ordered_set_ids[-1], *draw.ordered_set_ids[1:-1], draw.ordered_set_ids[0])
    with pytest.raises(ValueError, match="slice"):
        replace(result, draws=(replace(draw, ordered_set_ids=changed), *result.draws[1:]))


def test_restored_qualifiers_cannot_duplicate_place_or_season():
    from dataclasses import replace

    group = build_champions_groups(make_cohort(1), seed=7).groups[0]
    first = group.participants[0]
    other = next(p for p in group.participants if p.qualifying_place != first.qualifying_place)
    with pytest.raises(ValueError, match="places"):
        replace(
            group,
            participants=(
                replace(first, qualifying_place=other.qualifying_place),
                *group.participants[1:],
            ),
        )
    with pytest.raises(ValueError, match="season"):
        replace(
            group,
            participants=(
                replace(first, qualifying_season_id=UUID(int=9876)),
                *group.participants[1:],
            ),
        )
