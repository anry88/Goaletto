"""Unit checks for the pure champions-cup knockout bracket (AND-126)."""

from collections import Counter
from dataclasses import replace
from random import Random
from uuid import UUID, uuid5

import pytest

from goaletto.domain.champions_bracket import (
    ALLOWED_PARTICIPANT_COUNTS,
    CHAMPIONS_BRACKET_POLICY,
    CHAMPIONS_PHASE_DAY,
    TOTAL_CHAMPIONS_CYCLES,
    ChampionsBracket,
    all_matches,
    build_champions_bracket,
    champion,
    final_tie,
    free_cycles_for,
    legs_of,
    match_days_for,
    possible_clubs,
    possible_tie_clubs,
    resolve_slot,
    rounds_for,
    tie_by_id,
    tie_id_for,
    validate_against_group,
    validate_winners,
)
from goaletto.domain.champions_groups import (
    ChampionsGroup,
    ChampionsParticipant,
    build_champions_groups,
)
from goaletto.domain.champions_groups import (
    champions_cycles_for as groups_cycles_for,
)
from goaletto.domain.cup_bracket import club_slot, winner_slot
from goaletto.domain.knockout import (
    KNOCKOUT_POLICY,
    KnockoutTie,
    MatchResult,
    PenaltyKick,
    PenaltyShootout,
    decide_shootout,
    decide_tie,
)
from goaletto.domain.league_allocation import LeagueSnapshot
from goaletto.domain.neutral_final import (
    NEUTRAL_FINAL_POLICY,
    NeutralFinalSnapshot,
    settle_neutral_final,
)

NAMESPACE = UUID("6f2f0f3e-2c3a-4b5e-9f1a-7c8d9e0f1a2b")
TOURNAMENT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
OTHER_TOURNAMENT_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _group_uuid(kind: str, num_sets: int, index: int) -> UUID:
    return uuid5(NAMESPACE, f"bracket-{kind}:{num_sets}:{index}")


def make_group(num_sets: int, tier: int = 1, group_index: int = 0) -> ChampionsGroup:
    assert num_sets in (1, 2, 4, 8, 16, 32, 64)
    set_ids = tuple(
        sorted((_group_uuid("set", num_sets, i) for i in range(num_sets)), key=lambda v: v.hex)
    )
    collected: list[ChampionsParticipant] = []
    for set_id in set_ids:
        league_id = uuid5(set_id, "league:bracket")
        season_id = uuid5(set_id, "season:bracket")
        for place in range(1, 9):
            club_id = uuid5(league_id, f"club:{place}")
            collected.append(
                ChampionsParticipant(
                    participant_id=uuid5(club_id, "participant:bracket"),
                    club_id=club_id,
                    source_league_id=league_id,
                    source_league_set_id=set_id,
                    qualifying_season_id=season_id,
                    qualifying_tier=tier,
                    qualifying_place=place,
                )
            )
    collected.sort(key=lambda participant: participant.club_id.hex)
    participant_count = 8 * num_sets
    rounds = participant_count.bit_length() - 1
    days = 2 * rounds - 1
    return ChampionsGroup(
        id=_group_uuid("group", num_sets, group_index),
        tier=tier,
        group_index=group_index,
        league_set_ids=set_ids,
        participants=tuple(collected),
        participant_count=participant_count,
        rounds=rounds,
        match_days=days,
        champions_cycles=tuple(range(1, days + 1)),
        free_cycles=tuple(range(days + 1, TOTAL_CHAMPIONS_CYCLES + 1)),
        phase_day=CHAMPIONS_PHASE_DAY,
    )


def build(num_sets: int = 1, seed: int = 7, tier: int = 1) -> ChampionsBracket:
    return build_champions_bracket(
        group=make_group(num_sets, tier=tier), tournament_id=TOURNAMENT_ID, seed=seed
    )


def direct_clubs(bracket: ChampionsBracket) -> list[UUID]:
    found: list[UUID] = []
    for tie in bracket.ties:
        if tie.round_number == 1:
            for slot in (tie.home, tie.away):
                assert slot.club_id is not None
                found.append(slot.club_id)
    return found


def play_full(bracket: ChampionsBracket, seed: int) -> dict[UUID, UUID]:
    rng = Random(seed)
    winners: dict[UUID, UUID] = {}
    ordered = sorted(bracket.ties, key=lambda tie: (tie.round_number, tie.tie_index))
    for tie in ordered:
        pair: list[UUID] = []
        for slot in (tie.home, tie.away):
            if slot.kind == "club":
                assert slot.club_id is not None
                pair.append(slot.club_id)
            else:
                assert slot.tie_id is not None
                pair.append(winners[slot.tie_id])
        assert len(pair) == 2 and pair[0] != pair[1]
        winners[tie.id] = rng.choice(sorted(pair, key=lambda club: club.hex))
    validate_winners(bracket, winners)
    return winners


@pytest.mark.parametrize(
    ("num_sets", "participants", "rounds", "days", "ties", "matches"),
    [
        (1, 8, 3, 5, 7, 13),
        (2, 16, 4, 7, 15, 29),
        (4, 32, 5, 9, 31, 61),
        (8, 64, 6, 11, 63, 125),
        (16, 128, 7, 13, 127, 253),
        (32, 256, 8, 15, 255, 509),
        (64, 512, 9, 17, 511, 1021),
    ],
)
def test_structural_table_all_sizes(
    num_sets: int, participants: int, rounds: int, days: int, ties: int, matches: int
) -> None:
    assert participants in ALLOWED_PARTICIPANT_COUNTS
    assert rounds_for(participants) == rounds
    assert match_days_for(participants) == days
    bracket = build(num_sets=num_sets, seed=7)
    assert bracket.policy_version == CHAMPIONS_BRACKET_POLICY
    assert bracket.rounds == rounds
    assert bracket.match_days == days
    assert len(bracket.ties) == ties == participants - 1
    assert len(all_matches(bracket)) == matches == 2 * participants - 3
    assert bracket.champions_cycles == tuple(range(1, days + 1))
    assert bracket.free_cycles == tuple(range(days + 1, 19))
    assert bracket.free_cycles[-1] == 18
    assert bracket.phase_day == CHAMPIONS_PHASE_DAY
    assert set(bracket.participants) == set(direct_clubs(bracket))
    assert len(set(bracket.participants)) == participants


@pytest.mark.parametrize("num_sets", [1, 2, 4, 8, 16, 32, 64])
def test_round_layout_home_away_neutral_final(num_sets: int) -> None:
    bracket = build(num_sets=num_sets, seed=7)
    counts = Counter(tie.round_number for tie in bracket.ties)
    participant_count = 8 * num_sets
    total_rounds = participant_count.bit_length() - 1
    assert dict(counts) == {
        round_number: participant_count // (2**round_number)
        for round_number in range(1, total_rounds + 1)
    }
    for tie in bracket.ties:
        if tie.round_number < total_rounds:
            assert tie.champions_cycles == (2 * tie.round_number - 1, 2 * tie.round_number)
            assert tie.neutral is False
        else:
            assert tie.champions_cycles == (2 * total_rounds - 1,)
            assert tie.neutral is True
        legs = legs_of(tie)
        if tie.neutral:
            assert len(legs) == 1
            assert legs[0].champions_cycle == 2 * total_rounds - 1
            assert legs[0].neutral is True
            assert legs[0].phase_day == CHAMPIONS_PHASE_DAY
            assert legs[0].home == tie.home and legs[0].away == tie.away
        else:
            assert len(legs) == 2
            first, second = legs
            assert (first.champions_cycle, second.champions_cycle) == tie.champions_cycles
            assert first.home == tie.home and first.away == tie.away
            assert second.home == tie.away and second.away == tie.home
            assert first.neutral is False and second.neutral is False
            assert first.phase_day == second.phase_day == CHAMPIONS_PHASE_DAY
    final = final_tie(bracket)
    assert final.round_number == total_rounds
    assert final.neutral is True
    assert final.home.kind == "winner" and final.away.kind == "winner"


@pytest.mark.parametrize("num_sets", [1, 2, 4, 8, 16, 32, 64])
def test_no_matches_in_free_cycles(num_sets: int) -> None:
    bracket = build(num_sets=num_sets, seed=11)
    matches = all_matches(bracket)
    assert all(match.champions_cycle not in bracket.free_cycles for match in matches)
    assert all(match.champions_cycle != 18 for match in matches)
    assert bracket.free_cycles[-1] == 18
    # Per-cycle volumes: each two-leg round contributes its tie count per cycle.
    participant_count = 8 * num_sets
    total_rounds = participant_count.bit_length() - 1
    per_cycle = Counter(match.champions_cycle for match in matches)
    for round_number in range(1, total_rounds):
        expected = participant_count // (2**round_number)
        assert per_cycle[2 * round_number - 1] == expected
        assert per_cycle[2 * round_number] == expected
    assert per_cycle[2 * total_rounds - 1] == 1
    assert 2 * total_rounds not in per_cycle or total_rounds == 1  # final has no second leg
    assert 18 not in per_cycle


@pytest.mark.parametrize("num_sets", [1, 2, 4])
def test_seeded_full_runs_validate_and_elect_champion(num_sets: int) -> None:
    bracket = build(num_sets=num_sets, seed=7)
    winners = play_full(bracket, seed=99)
    assert len(winners) == len(bracket.ties)
    champ = champion(bracket, winners)
    assert champ is not None
    assert champ in set(bracket.participants)
    assert champ == winners[final_tie(bracket).id]


def test_large_bracket_full_run_to_champion() -> None:
    bracket = build(num_sets=64, seed=7)
    assert len(bracket.ties) == 511
    assert len(all_matches(bracket)) == 1021
    winners = play_full(bracket, seed=1234)
    assert champion(bracket, winners) in set(bracket.participants)


def test_stable_seed_replay_and_input_permutation() -> None:
    first = build(num_sets=2, seed=7)
    second = build(num_sets=2, seed=7)
    assert first == second
    assert [tie.id for tie in first.ties] == [tie.id for tie in second.ties]

    other_seed = build(num_sets=2, seed=8)
    # Tie IDs are structural in tournament/group namespace; the draw moves.
    assert {tie.id for tie in other_seed.ties} == {tie.id for tie in first.ties}
    assert any(
        (left.home, left.away) != (right.home, right.away)
        for left, right in zip(first.ties, other_seed.ties, strict=True)
    )

    # Permuting the group participant order must not change the draw.
    group = make_group(2)
    shuffled_participants = list(group.participants)
    Random(3).shuffle(shuffled_participants)
    assert tuple(shuffled_participants) != group.participants
    permuted_group = replace(group, participants=tuple(shuffled_participants))
    permuted = build_champions_bracket(group=permuted_group, tournament_id=TOURNAMENT_ID, seed=7)
    assert permuted == first

    # Tournament ID namespaces stable IDs; it is not a participant season.
    other_tournament = build_champions_bracket(
        group=make_group(2), tournament_id=OTHER_TOURNAMENT_ID, seed=7
    )
    assert {tie.id for tie in other_tournament.ties} != {tie.id for tie in first.ties}
    for tie in other_tournament.ties:
        assert tie.id == tie_id_for(OTHER_TOURNAMENT_ID, group.id, tie.round_number, tie.tie_index)


def test_tie_ids_use_tournament_group_namespace() -> None:
    group = make_group(1)
    bracket = build_champions_bracket(group=group, tournament_id=TOURNAMENT_ID, seed=5)
    for tie in bracket.ties:
        assert tie.id == tie_id_for(TOURNAMENT_ID, group.id, tie.round_number, tie.tie_index)
    with pytest.raises(ValueError):
        tie_id_for("not-a-uuid", group.id, 1, 0)  # type: ignore[arg-type]


def test_possible_clubs_before_and_after_elimination() -> None:
    bracket = build(num_sets=1, seed=7)
    final = final_tie(bracket)
    assert possible_tie_clubs(bracket, final.id, {}) == set(bracket.participants)
    assert champion(bracket, {}) is None

    first_tie = next(tie for tie in bracket.ties if tie.round_number == 1)
    assert first_tie.home.club_id is not None and first_tie.away.club_id is not None
    home_club = first_tie.home.club_id
    away_club = first_tie.away.club_id

    consumer = next(
        tie
        for tie in bracket.ties
        if any(
            slot.kind == "winner" and slot.tie_id == first_tie.id for slot in (tie.home, tie.away)
        )
    )
    before = possible_tie_clubs(bracket, consumer.id, {})
    assert home_club in before and away_club in before

    winners = {first_tie.id: home_club}
    validate_winners(bracket, winners)
    after = possible_tie_clubs(bracket, consumer.id, winners)
    assert home_club in after
    assert away_club not in after
    other_slot = consumer.away if consumer.home.tie_id == first_tie.id else consumer.home
    survivor = possible_clubs(bracket, other_slot, winners)
    assert survivor <= after and len(survivor) > 0

    assert resolve_slot(bracket, first_tie.home, {}) == home_club
    feeder_slot = winner_slot(first_tie.id)
    assert resolve_slot(bracket, feeder_slot, {}) is None
    assert resolve_slot(bracket, feeder_slot, winners) == home_club


def test_bracket_matches_frozen_group() -> None:
    bracket = build(num_sets=1, seed=7)
    group = make_group(1)
    validate_against_group(bracket, group)
    other_group = make_group(1, tier=2)
    with pytest.raises(ValueError):
        validate_against_group(bracket, other_group)
    foreign_club = uuid5(NAMESPACE, "foreign-club")
    with pytest.raises(ValueError):
        broken = replace(bracket, participants=(*bracket.participants[1:], foreign_club))
        validate_against_group(broken, group)


def test_real_champions_group_feeds_bracket() -> None:
    namespace = UUID("6f2f0f3e-2c3a-4b5e-9f1a-7c8d9e0f1a2b")
    set_id = uuid5(namespace, "real-set:0")
    season_id = uuid5(set_id, "season:completed")
    snapshots: list[LeagueSnapshot] = []
    for tier in range(1, 6):
        league_id = uuid5(set_id, f"league:{tier}")
        order = tuple(uuid5(league_id, f"club:{place}") for place in range(1, 11))
        snapshots.append(
            LeagueSnapshot(
                cohort_key="real-cohort",
                season_id=season_id,
                league_id=league_id,
                league_set_id=set_id,
                tier=tier,
                order=order,
            )
        )
    result = build_champions_groups(snapshots, seed=7)
    group = next(group for group in result.groups if group.tier == 1)
    assert group.champions_cycles == groups_cycles_for(1)
    bracket = build_champions_bracket(group=group, tournament_id=TOURNAMENT_ID, seed=7)
    validate_against_group(bracket, group)
    assert set(bracket.participants) == {participant.club_id for participant in group.participants}
    # Places 9..10 never enter the bracket.
    excluded = set(snapshots[0].order[8:])
    assert not (set(bracket.participants) & excluded)


def test_invalid_inputs_rejected() -> None:
    group = make_group(1)
    with pytest.raises(ValueError):
        build_champions_bracket(group=group, tournament_id="not-a-uuid", seed=0)  # type: ignore[arg-type]
    for bad_seed in (-1, True, False, 2**63, "7"):
        with pytest.raises(ValueError):
            build_champions_bracket(group=group, tournament_id=TOURNAMENT_ID, seed=bad_seed)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        build_champions_bracket(group="not-a-group", tournament_id=TOURNAMENT_ID, seed=0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        rounds_for(7)
    with pytest.raises(ValueError):
        match_days_for(24)
    with pytest.raises(ValueError):
        free_cycles_for(7)
    with pytest.raises(KeyError):
        tie_by_id(build(), uuid5(NAMESPACE, "missing-tie"))
    with pytest.raises(ValueError):
        club_slot("not-a-uuid")  # type: ignore[arg-type]


def test_invalid_winners_rejected() -> None:
    bracket = build(num_sets=1, seed=7)
    first = next(tie for tie in bracket.ties if tie.round_number == 1)
    assert first.home.club_id is not None and first.away.club_id is not None
    foreign = uuid5(NAMESPACE, "foreign-club")
    unknown_tie = uuid5(NAMESPACE, "unknown-tie")

    with pytest.raises(ValueError, match="[Uu]nknown tie"):
        validate_winners(bracket, {unknown_tie: first.home.club_id})
    with pytest.raises(ValueError, match="participant"):
        validate_winners(bracket, {first.id: foreign})

    other_first = next(tie for tie in bracket.ties if tie.round_number == 1 and tie.id != first.id)
    assert other_first.home.club_id is not None
    # Winner from another subtree cannot win this tie.
    with pytest.raises(ValueError):
        validate_winners(bracket, {first.id: other_first.home.club_id})

    consumer = next(
        tie
        for tie in bracket.ties
        if any(slot.kind == "winner" and slot.tie_id == first.id for slot in (tie.home, tie.away))
    )
    outsider = next(
        club
        for club in bracket.participants
        if club not in possible_tie_clubs(bracket, consumer.id, {})
    )
    with pytest.raises(ValueError):
        validate_winners(bracket, {first.id: first.home.club_id, consumer.id: outsider})

    # Duplicated winner on unrelated branches.
    with pytest.raises(ValueError):
        validate_winners(
            bracket, {first.id: first.home.club_id, other_first.id: first.home.club_id}
        )

    # Draws of single matches are never winners.
    with pytest.raises(ValueError):
        validate_winners(bracket, {first.id: None})  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        resolve_slot(bracket, winner_slot(first.id), {first.id: None})  # type: ignore[dict-value]

    with pytest.raises(ValueError, match="participant"):
        resolve_slot(bracket, club_slot(foreign), {})
    with pytest.raises(ValueError, match="[Uu]nknown tie"):
        resolve_slot(bracket, winner_slot(unknown_tie), {})


@pytest.mark.parametrize("reader", ["resolve", "possible", "tie", "champion"])
def test_all_readers_reject_a_winner_from_another_branch(reader: str) -> None:
    bracket = build(num_sets=1, seed=7)
    tie = next(t for t in bracket.ties if t.round_number == 1)
    other = next(t for t in bracket.ties if t.round_number == 1 and t.id != tie.id)
    assert other.home.club_id is not None
    winners = {tie.id: other.home.club_id}
    with pytest.raises(ValueError):
        if reader == "resolve":
            resolve_slot(bracket, winner_slot(tie.id), winners)
        elif reader == "possible":
            possible_clubs(bracket, winner_slot(tie.id), winners)
        elif reader == "tie":
            possible_tie_clubs(bracket, final_tie(bracket).id, winners)
        else:
            champion(bracket, winners)


def test_champion_requires_resolved_ancestors_and_losers_cannot_return() -> None:
    bracket = build(num_sets=1, seed=7)
    final = final_tie(bracket)
    entrant = bracket.participants[0]
    with pytest.raises(ValueError, match="feeder"):
        champion(bracket, {final.id: entrant})
    winners = play_full(bracket, seed=56)
    first = next(tie for tie in bracket.ties if tie.round_number == 1)
    loser = next(
        club for club in (first.home.club_id, first.away.club_id) if club != winners[first.id]
    )
    assert loser is not None
    winners[final.id] = loser
    with pytest.raises(ValueError):
        possible_clubs(bracket, winner_slot(final.id), winners)


@pytest.mark.parametrize(
    "damage", ["missing", "entrant", "shared_feeder", "same_opponents", "cycles", "final"]
)
def test_restored_bracket_rejects_invalid_full_graph(damage: str) -> None:
    bracket = build(num_sets=1, seed=7)
    with pytest.raises(ValueError):
        ties = list(bracket.ties)
        if damage == "missing":
            ties.pop()
        elif damage == "entrant":
            ties[0] = replace(ties[0], home=club_slot(uuid5(NAMESPACE, "not-a-participant")))
        elif damage == "shared_feeder":
            # Reuse one round-1 feeder in two round-2 ties.
            round2 = [tie for tie in ties if tie.round_number == 2]
            assert len(round2) == 2
            first_feeder = round2[0].home
            second_index = ties.index(round2[1])
            ties[second_index] = replace(round2[1], home=first_feeder)
        elif damage == "same_opponents":
            ties[0] = replace(ties[0], away=ties[0].home)
        elif damage == "cycles":
            ties[0] = replace(ties[0], champions_cycles=(3, 4))
        else:
            finals = [tie for tie in ties if tie.round_number == bracket.rounds]
            assert len(finals) == 1
            ties.remove(finals[0])
        replace(bracket, ties=tuple(ties))


def test_knockout_winners_with_extra_time_and_pens_feed_bracket() -> None:
    bracket = build(num_sets=1, seed=7)
    first = next(tie for tie in bracket.ties if tie.round_number == 1)
    assert first.home.club_id is not None and first.away.club_id is not None
    club_a = first.home.club_id
    club_b = first.away.club_id

    # Two-leg tie decided in regulation: A wins 3:1 on aggregate.
    regulation = KnockoutTie(
        policy_version=KNOCKOUT_POLICY, club_a_id=club_a, club_b_id=club_b, tie_format="two-leg"
    )
    decided = decide_tie(
        tie=regulation,
        first_match=MatchResult(
            segment="leg1",
            home_id=club_a,
            away_id=club_b,
            home_goals=2,
            away_goals=0,
            completed=True,
        ),
        second_match=MatchResult(
            segment="leg2",
            home_id=club_b,
            away_id=club_a,
            home_goals=1,
            away_goals=1,
            completed=True,
        ),
    )
    assert decided.status == "decided" and decided.winner == club_a
    validate_winners(bracket, {first.id: decided.winner})

    # Same pair forced to extra time and penalties: tied 2:2, scoreless ET, A wins the shootout.
    tied = decide_tie(
        tie=regulation,
        first_match=MatchResult(
            segment="leg1",
            home_id=club_a,
            away_id=club_b,
            home_goals=1,
            away_goals=0,
            completed=True,
        ),
        second_match=MatchResult(
            segment="leg2",
            home_id=club_b,
            away_id=club_a,
            home_goals=1,
            away_goals=0,
            completed=True,
        ),
    )
    assert tied.status == "await_extra_time"
    shootout = PenaltyShootout(club_a_id=club_a, club_b_id=club_b, first_kicker_id=club_a, kicks=())
    kicks = (
        PenaltyKick(kick_number=1, club_id=club_a, scored=True),
        PenaltyKick(kick_number=2, club_id=club_b, scored=False),
        PenaltyKick(kick_number=3, club_id=club_a, scored=True),
        PenaltyKick(kick_number=4, club_id=club_b, scored=False),
        PenaltyKick(kick_number=5, club_id=club_a, scored=True),
        PenaltyKick(kick_number=6, club_id=club_b, scored=False),
    )
    shootout = PenaltyShootout(
        club_a_id=club_a, club_b_id=club_b, first_kicker_id=club_a, kicks=kicks
    )
    shootout_result = decide_shootout(shootout)
    assert shootout_result.status == "decided" and shootout_result.winner == club_a
    with_extra = decide_tie(
        tie=regulation,
        first_match=MatchResult(
            segment="leg1",
            home_id=club_a,
            away_id=club_b,
            home_goals=1,
            away_goals=0,
            completed=True,
        ),
        second_match=MatchResult(
            segment="leg2",
            home_id=club_b,
            away_id=club_a,
            home_goals=1,
            away_goals=0,
            completed=True,
        ),
        extra_time=MatchResult(
            segment="leg2-et",
            home_id=club_b,
            away_id=club_a,
            home_goals=0,
            away_goals=0,
            completed=True,
        ),
        shootout=shootout,
    )
    assert with_extra.status == "decided" and with_extra.winner == club_a
    assert with_extra.aggregate_a == with_extra.aggregate_b == 1
    assert with_extra.penalty_a == 3 and with_extra.penalty_b == 0
    validate_winners(bracket, {first.id: with_extra.winner})

    # An undecided knockout tie must never be recorded as a bracket winner.
    undecided = decide_tie(
        tie=regulation,
        first_match=MatchResult(
            segment="leg1",
            home_id=club_a,
            away_id=club_b,
            home_goals=1,
            away_goals=1,
            completed=True,
        ),
    )
    assert undecided.winner is None
    with pytest.raises(ValueError):
        validate_winners(bracket, {first.id: club_a, final_tie(bracket).id: club_a})

    # Single neutral final through extra time: the same knockout API decides it.
    final = final_tie(bracket)
    assert final.neutral is True
    finalists = sorted(possible_tie_clubs(bracket, final.id, {}), key=lambda club: club.hex)[:2]
    assert len(finalists) == 2
    single = KnockoutTie(
        policy_version=KNOCKOUT_POLICY,
        club_a_id=finalists[0],
        club_b_id=finalists[1],
        tie_format="single",
    )
    final_decision = decide_tie(
        tie=single,
        first_match=MatchResult(
            segment="final",
            home_id=finalists[0],
            away_id=finalists[1],
            home_goals=1,
            away_goals=1,
            completed=True,
        ),
        extra_time=MatchResult(
            segment="final-et",
            home_id=finalists[0],
            away_id=finalists[1],
            home_goals=1,
            away_goals=0,
            completed=True,
        ),
    )
    assert final_decision.status == "decided" and final_decision.winner == finalists[0]


def test_known_finalists_settle_neutral_final_split() -> None:
    bracket = build(num_sets=1, seed=7)
    winners = play_full(bracket, seed=2026)
    final = final_tie(bracket)
    assert final.neutral is True
    # Two known finalists: resolve both feeder winners of the final.
    assert final.home.tie_id is not None and final.away.tie_id is not None
    finalist_a = winners[final.home.tie_id]
    finalist_b = winners[final.away.tie_id]
    assert finalist_a != finalist_b
    snapshot = NeutralFinalSnapshot(
        policy_version=NEUTRAL_FINAL_POLICY,
        venue_id=uuid5(NAMESPACE, "neutral-venue"),
        capacity=50_000,
        ticket_price=5,
        club_a_id=finalist_a,
        club_b_id=finalist_b,
        supporters_a=2_500,
        supporters_b=2_500,
        seed=7,
        neutral=True,
    )
    result = settle_neutral_final(snapshot)
    assert result.payout_a + result.payout_b == result.gross
    assert result.neutral is True and result.home_advantage == 0
    assert champion(bracket, winners) in (finalist_a, finalist_b)


def test_bracket_helpers_reject_bad_shapes() -> None:
    bracket = build(num_sets=1, seed=7)
    sample = bracket.ties[0]
    assert tie_by_id(bracket, sample.id) == sample
    with pytest.raises(AttributeError):
        bracket.ties.append(sample)  # type: ignore[attr-defined]
    leg = legs_of(sample)[0]
    with pytest.raises(ValueError):
        replace(leg, champions_cycle=18)
    with pytest.raises(ValueError):
        replace(sample, champions_cycles=(18,))
