"""Unit checks for the pure 50-club cup bracket (AND-123)."""

from collections import Counter
from random import Random
from uuid import UUID, uuid5

import pytest

from goaletto.domain.cup_bracket import (
    CUP_POLICY,
    FINAL_CUP_DAY,
    RESERVE_CUP_DAY,
    TOTAL_CLUBS,
    TOTAL_MATCHES,
    TOTAL_TIES,
    CupBracket,
    CupEntry,
    CupSlot,
    CupTie,
    all_matches,
    build_bracket,
    champion,
    club_slot,
    final_tie,
    legs_of,
    possible_clubs,
    possible_tie_clubs,
    resolve_slot,
    tie_by_id,
    validate_winners,
    winner_slot,
)

NAMESPACE = UUID("6f2f0f3e-2c3a-4b5e-9f1a-7c8d9e0f1a2b")
SEASON_ID = UUID("11111111-1111-1111-1111-111111111111")
SET_ID = UUID("22222222-2222-2222-2222-222222222222")

EXPECTED_COUNTS = {
    "prelim": 4,
    "entry-16": 8,
    "entry-8": 4,
    "entry-4": 2,
    "round-32": 16,
    "round-16": 8,
    "quarter": 4,
    "semi": 2,
    "final": 1,
}

EXPECTED_DAYS = {
    "prelim": (1, 2),
    "entry-16": (3, 4),
    "entry-8": (5, 6),
    "entry-4": (7, 8),
    "round-32": (9, 10),
    "round-16": (11, 12),
    "quarter": (13, 14),
    "semi": (15, 16),
    "final": (17,),
}

EXPECTED_DAY_MATCHES = {
    1: 4,
    2: 4,
    3: 8,
    4: 8,
    5: 4,
    6: 4,
    7: 2,
    8: 2,
    9: 16,
    10: 16,
    11: 8,
    12: 8,
    13: 4,
    14: 4,
    15: 2,
    16: 2,
    17: 1,
}


def make_entries() -> list[CupEntry]:
    entries: list[CupEntry] = []
    for tier in range(1, 6):
        league_id = uuid5(NAMESPACE, f"cup-league:{tier}")
        order = tuple(uuid5(league_id, f"club:{place}") for place in range(1, 11))
        entries.append(CupEntry(league_id=league_id, tier=tier, order=order))
    return entries


def build(seed: int = 7) -> CupBracket:
    return build_bracket(
        season_id=SEASON_ID, league_set_id=SET_ID, entries=make_entries(), seed=seed
    )


def direct_clubs(bracket: CupBracket) -> list[UUID]:
    found: list[UUID] = []
    for tie in bracket.ties:
        for slot in (tie.home, tie.away):
            if slot.kind == "club" and slot.club_id is not None:
                found.append(slot.club_id)
    return found


def play_full(bracket: CupBracket, seed: int) -> dict[UUID, UUID]:
    rng = Random(seed)
    winners: dict[UUID, UUID] = {}
    ordered = sorted(bracket.ties, key=lambda t: (t.cup_days[0], t.id.hex))
    for tie in ordered:
        options = sorted(possible_tie_clubs(bracket, tie.id, winners), key=lambda c: c.hex)
        assert len(options) == 2, (
            f"tie {tie.round_key} should offer exactly two clubs, got {len(options)}"
        )
        winners[tie.id] = rng.choice(options)
    validate_winners(bracket, winners)
    return winners


def test_full_counts_days_and_entry_split() -> None:
    bracket = build()
    assert bracket.policy_version == CUP_POLICY
    assert len(bracket.ties) == TOTAL_TIES == 49
    assert len(all_matches(bracket)) == TOTAL_MATCHES == 97

    counts = Counter(tie.round_key for tie in bracket.ties)
    assert dict(counts) == EXPECTED_COUNTS

    for tie in bracket.ties:
        assert tie.cup_days == EXPECTED_DAYS[tie.round_key]
        assert RESERVE_CUP_DAY not in tie.cup_days
        assert tie.round_label.strip()

    clubs = direct_clubs(bracket)
    assert len(clubs) == TOTAL_CLUBS == 50
    assert len(set(clubs)) == 50

    by_tier = {entry.tier: entry for entry in make_entries()}
    prelim_direct = {
        slot.club_id
        for tie in bracket.ties
        if tie.round_key == "prelim"
        for slot in (tie.home, tie.away)
        if slot.club_id is not None
    }
    assert prelim_direct == set(by_tier[5].order[2:])

    entry16_direct = {
        slot.club_id
        for tie in bracket.ties
        if tie.round_key == "entry-16"
        for slot in (tie.home, tie.away)
        if slot.kind == "club" and slot.club_id is not None
    }
    assert entry16_direct == set((*by_tier[5].order[:2], *by_tier[4].order))

    round32_direct = {
        slot.club_id
        for tie in bracket.ties
        if tie.round_key == "round-32"
        for slot in (tie.home, tie.away)
        if slot.club_id is not None
    }
    assert round32_direct == set((*by_tier[1].order, *by_tier[2].order, *by_tier[3].order))

    # No other round introduces direct entrants.
    for tie in bracket.ties:
        if tie.round_key not in ("prelim", "entry-16", "round-32"):
            assert tie.home.kind == "winner"
            assert tie.away.kind == "winner"

    # Final is a single neutral match on day 17.
    final = final_tie(bracket)
    assert final.cup_days == (FINAL_CUP_DAY,) == (17,)
    assert final.neutral is True
    assert final.home.kind == "winner" and final.away.kind == "winner"


def test_home_away_exchange_and_day_volumes() -> None:
    bracket = build()
    for tie in bracket.ties:
        legs = legs_of(tie)
        if tie.round_key == "final":
            assert len(legs) == 1
            assert legs[0].cup_day == 17
            assert legs[0].neutral is True
            assert legs[0].home == tie.home and legs[0].away == tie.away
        else:
            assert len(legs) == 2
            first, second = legs
            assert (first.cup_day, second.cup_day) == tie.cup_days
            assert first.home == tie.home and first.away == tie.away
            assert second.home == tie.away and second.away == tie.home
            assert first.neutral is False and second.neutral is False

    matches = all_matches(bracket)
    assert len(matches) == 97
    assert all(match.cup_day != RESERVE_CUP_DAY for match in matches)
    per_day = Counter(match.cup_day for match in matches)
    assert dict(per_day) == EXPECTED_DAY_MATCHES


def test_longest_path_is_seventeen_matches() -> None:
    bracket = build()
    # Downstream map: feeder tie -> downstream ties.
    downstream: dict[UUID, list[UUID]] = {tie.id: [] for tie in bracket.ties}
    for tie in bracket.ties:
        for slot in (tie.home, tie.away):
            if slot.kind == "winner" and slot.tie_id is not None:
                downstream[slot.tie_id].append(tie.id)

    def chain_matches(start: UUID) -> int:
        # Each visited tie contributes 2 matches except the final (1).
        total = 0
        current = start
        while True:
            tie = tie_by_id(bracket, current)
            total += 1 if tie.round_key == "final" else 2
            followers = downstream[current]
            assert len(followers) <= 1, "each tie feeds at most one downstream tie"
            if not followers:
                return total
            current = followers[0]

    prelim_ids = [tie.id for tie in bracket.ties if tie.round_key == "prelim"]
    longest = max(chain_matches(tie_id) for tie_id in prelim_ids)
    assert longest == 17

    round32_ids = [tie.id for tie in bracket.ties if tie.round_key == "round-32"]
    latest_entry = max(chain_matches(tie_id) for tie_id in round32_ids)
    # Late entrants (tiers 1..3) play 2+2+2+2+1 = 9 matches to lift the cup.
    assert latest_entry == 9


@pytest.mark.parametrize("seed", [0, 1, 42, 2**62])
def test_seeded_full_runs_validate_and_elect_champion(seed: int) -> None:
    bracket = build(seed=seed)
    winners = play_full(bracket, seed=seed + 1000)
    assert len(winners) == 49
    champ = champion(bracket, winners)
    assert champ is not None
    assert champ in set(direct_clubs(bracket))
    # Progression allows the same club on one lineage only.
    assert champ == winners[final_tie(bracket).id]


def test_stable_ids_replay_and_input_permutation() -> None:
    first = build(seed=7)
    second = build(seed=7)
    assert first == second
    assert [tie.id for tie in first.ties] == [tie.id for tie in second.ties]

    other_seed = build(seed=8)
    # Tie IDs are structural (seed-independent); the draw assignment moves.
    assert {tie.id for tie in other_seed.ties} == {tie.id for tie in first.ties}
    changed = sum(
        1
        for left, right in zip(first.ties, other_seed.ties, strict=True)
        if (left.home, left.away) != (right.home, right.away)
    )
    assert changed > 0

    entries = make_entries()
    shuffled = list(entries)
    Random(3).shuffle(shuffled)
    permuted = build_bracket(season_id=SEASON_ID, league_set_id=SET_ID, entries=shuffled, seed=7)
    assert permuted == first


def test_possible_clubs_before_and_after_elimination() -> None:
    bracket = build()
    final = final_tie(bracket)
    assert possible_tie_clubs(bracket, final.id, {}) == set(direct_clubs(bracket))
    assert champion(bracket, {}) is None

    prelim_tie = next(tie for tie in bracket.ties if tie.round_key == "prelim")
    assert prelim_tie.home.kind == "club" and prelim_tie.away.kind == "club"
    assert prelim_tie.home.club_id is not None and prelim_tie.away.club_id is not None
    home_club = prelim_tie.home.club_id
    away_club = prelim_tie.away.club_id

    # The single downstream tie consuming this prelim winner.
    consumer = next(
        tie
        for tie in bracket.ties
        if any(
            slot.kind == "winner" and slot.tie_id == prelim_tie.id for slot in (tie.home, tie.away)
        )
    )
    before = possible_tie_clubs(bracket, consumer.id, {})
    assert home_club in before and away_club in before

    winners = {prelim_tie.id: home_club}
    validate_winners(bracket, winners)
    after = possible_tie_clubs(bracket, consumer.id, winners)
    assert home_club in after
    assert away_club not in after
    # The other branch of the consumer survives untouched.
    other_slot = (
        consumer.away
        if consumer.home.kind == "winner" and (consumer.home.tie_id == prelim_tie.id)
        else consumer.home
    )
    if other_slot.kind == "club":
        assert other_slot.club_id in after
    else:
        assert other_slot.tie_id is not None
        survivor = possible_clubs(bracket, other_slot, winners)
        assert survivor <= after and len(survivor) > 0

    # Slot resolution mirrors the same story.
    assert resolve_slot(bracket, prelim_tie.home, {}) == home_club
    feeder_slot = winner_slot(prelim_tie.id)
    assert resolve_slot(bracket, feeder_slot, {}) is None
    assert resolve_slot(bracket, feeder_slot, winners) == home_club


def test_entry_rank_is_trusted_not_recomputed() -> None:
    entries = make_entries()
    bracket = build(seed=7)
    by_tier = {entry.tier: entry for entry in entries}
    assert {c for c in by_tier[5].order[2:]} == {
        slot.club_id
        for tie in bracket.ties
        if tie.round_key == "prelim"
        for slot in (tie.home, tie.away)
    }

    reversed_fifth = CupEntry(
        league_id=by_tier[5].league_id, tier=5, order=tuple(reversed(by_tier[5].order))
    )
    altered = [e for e in entries if e.tier != 5] + [reversed_fifth]
    rebuilt = build_bracket(season_id=SEASON_ID, league_set_id=SET_ID, entries=altered, seed=7)
    new_prelim = {
        slot.club_id
        for tie in rebuilt.ties
        if tie.round_key == "prelim"
        for slot in (tie.home, tie.away)
    }
    # Reversing places swaps which clubs start early: the module trusts order.
    assert new_prelim == set(reversed_fifth.order[2:])
    assert new_prelim != set(by_tier[5].order[2:])


def test_invalid_inputs_rejected() -> None:
    entries = make_entries()
    with pytest.raises(ValueError):
        build_bracket(season_id=SEASON_ID, league_set_id=SET_ID, entries=entries[:4], seed=0)
    with pytest.raises(ValueError):
        build_bracket(
            season_id="not-a-uuid",  # type: ignore[arg-type]
            league_set_id=SET_ID,
            entries=entries,
            seed=0,
        )
    for bad_seed in (-1, True, False, 2**63, "7"):
        with pytest.raises(ValueError):
            build_bracket(
                season_id=SEASON_ID,
                league_set_id=SET_ID,
                entries=entries,
                seed=bad_seed,  # type: ignore[arg-type]
            )
    # Duplicated league.
    with pytest.raises(ValueError):
        build_bracket(
            season_id=SEASON_ID,
            league_set_id=SET_ID,
            entries=[*entries[:4], entries[0]],
            seed=0,
        )
    # Duplicated club across leagues.
    tampered = CupEntry(league_id=entries[1].league_id, tier=2, order=entries[0].order)
    with pytest.raises(ValueError):
        build_bracket(
            season_id=SEASON_ID,
            league_set_id=SET_ID,
            entries=[entries[0], tampered, *entries[2:]],
            seed=0,
        )
    # Bad entry shapes.
    with pytest.raises(ValueError):
        CupEntry(league_id=entries[0].league_id, tier=6, order=entries[0].order)
    with pytest.raises(ValueError):
        CupEntry(league_id=entries[0].league_id, tier=1, order=entries[0].order[:9])
    with pytest.raises(ValueError):
        CupSlot(kind="club", club_id=None, tie_id=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        club_slot("not-a-uuid")  # type: ignore[arg-type]
    with pytest.raises(KeyError):
        tie_by_id(build(), uuid5(NAMESPACE, "missing-tie"))


def test_invalid_winners_rejected() -> None:
    bracket = build()
    prelim = next(tie for tie in bracket.ties if tie.round_key == "prelim")
    assert prelim.home.club_id is not None and prelim.away.club_id is not None
    foreign = uuid5(NAMESPACE, "foreign-club")
    unknown_tie = uuid5(NAMESPACE, "unknown-tie")

    with pytest.raises(ValueError, match="[Uu]nknown tie"):
        validate_winners(bracket, {unknown_tie: prelim.home.club_id})
    with pytest.raises(ValueError, match="entrant"):
        validate_winners(bracket, {prelim.id: foreign})

    # A tier-1 club cannot win a prelim tie whose subtree holds only tier-5 clubs.
    tier1_club = next(entry.order[0] for entry in bracket.entries if entry.tier == 1)
    with pytest.raises(ValueError):
        validate_winners(bracket, {prelim.id: tier1_club})

    # Incompatible downstream: prelim won by home, downstream won by an outsider.
    consumer = next(
        tie
        for tie in bracket.ties
        if any(slot.kind == "winner" and slot.tie_id == prelim.id for slot in (tie.home, tie.away))
    )
    outsider = next(
        c for c in direct_clubs(bracket) if c not in possible_tie_clubs(bracket, consumer.id, {})
    )
    # Outsider exists because the consumer covers only a slice of the 50.
    assert outsider not in possible_tie_clubs(bracket, consumer.id, {})
    with pytest.raises(ValueError):
        validate_winners(bracket, {prelim.id: prelim.home.club_id, consumer.id: outsider})

    # Duplicated winner on unrelated branches.
    other_prelim = next(
        tie for tie in bracket.ties if tie.round_key == "prelim" and tie.id != prelim.id
    )
    assert other_prelim.home.club_id is not None
    with pytest.raises(ValueError):
        validate_winners(
            bracket,
            {prelim.id: prelim.home.club_id, other_prelim.id: prelim.home.club_id},
        )

    # Draws are never winners.
    with pytest.raises(ValueError):
        validate_winners(bracket, {prelim.id: None})  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        resolve_slot(bracket, winner_slot(prelim.id), {prelim.id: None})  # type: ignore[dict-value]

    # Foreign slot clubs never resolve.
    with pytest.raises(ValueError, match="entrant"):
        resolve_slot(bracket, club_slot(foreign), {})
    with pytest.raises(ValueError, match="[Uu]nknown tie"):
        resolve_slot(bracket, winner_slot(unknown_tie), {})


def test_dependency_cycle_rejected() -> None:
    club_a = uuid5(NAMESPACE, "cycle-a")
    club_b = uuid5(NAMESPACE, "cycle-b")
    tie_one = uuid5(NAMESPACE, "cycle-tie-1")
    tie_two = uuid5(NAMESPACE, "cycle-tie-2")
    ties = (
        CupTie(
            id=tie_one,
            round_key="prelim",
            round_label="Prelim 8→4",
            cup_days=(1, 2),
            home=winner_slot(tie_two),
            away=club_slot(club_a),
            neutral=False,
        ),
        CupTie(
            id=tie_two,
            round_key="entry-16",
            round_label="Entry round 16→8",
            cup_days=(3, 4),
            home=winner_slot(tie_one),
            away=club_slot(club_b),
            neutral=False,
        ),
    )
    with pytest.raises(ValueError, match="cycle"):
        CupBracket(
            policy_version="cup-bracket-v1",
            season_id=SEASON_ID,
            league_set_id=SET_ID,
            seed=0,
            entries=tuple(make_entries()),
            ties=ties,
        )


def test_no_reserve_day_match_and_bracket_helpers() -> None:
    bracket = build(seed=11)
    assert all(tie.cup_days[0] != RESERVE_CUP_DAY for tie in bracket.ties)
    assert all(match.cup_day != RESERVE_CUP_DAY for match in all_matches(bracket))
    assert final_tie(bracket).cup_days == (17,)
    sample = bracket.ties[0]
    assert tie_by_id(bracket, sample.id) == sample
    with pytest.raises(AttributeError):
        bracket.ties.append(sample)  # type: ignore[attr-defined]
