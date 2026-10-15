"""Unit checks for the pure knockout tie resolution (AND-124 subpackage)."""

import dataclasses
from uuid import UUID, uuid4, uuid5

import pytest

from goaletto.domain.cup_bracket import CupEntry, build_bracket, final_tie, validate_winners
from goaletto.domain.knockout import (
    EXTRA_TIME_TICKS,
    KNOCKOUT_POLICY,
    REGULATION_TICKS,
    KnockoutTie,
    MatchResult,
    PenaltyKick,
    PenaltyShootout,
    apply_penalty_kick,
    decide_shootout,
    decide_tie,
)

CLUB_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
CLUB_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
CLUB_C = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


def make_tie(
    club_a: UUID = CLUB_A,
    club_b: UUID = CLUB_B,
    tie_format: str = "two-leg",
) -> KnockoutTie:
    return KnockoutTie(
        policy_version=KNOCKOUT_POLICY,
        club_a_id=club_a,
        club_b_id=club_b,
        tie_format=tie_format,  # type: ignore[arg-type]
    )


def make_match(
    segment: str,
    home: UUID,
    away: UUID,
    home_goals: int,
    away_goals: int,
    completed: bool = True,
) -> MatchResult:
    return MatchResult(
        segment=segment,  # type: ignore[arg-type]
        home_id=home,
        away_id=away,
        home_goals=home_goals,
        away_goals=away_goals,
        completed=completed,
    )


def leg1(home_goals: int, away_goals: int, completed: bool = True) -> MatchResult:
    return make_match("leg1", CLUB_A, CLUB_B, home_goals, away_goals, completed)


def leg2(home_goals_b: int, away_goals_a: int, completed: bool = True) -> MatchResult:
    # Second leg is hosted by club B.
    return make_match("leg2", CLUB_B, CLUB_A, home_goals_b, away_goals_a, completed)


def leg2_et(home_goals_b: int, away_goals_a: int, completed: bool = True) -> MatchResult:
    return make_match("leg2-et", CLUB_B, CLUB_A, home_goals_b, away_goals_a, completed)


def final(home_goals: int, away_goals: int, completed: bool = True) -> MatchResult:
    return make_match("final", CLUB_A, CLUB_B, home_goals, away_goals, completed)


def final_et(home_goals: int, away_goals: int, completed: bool = True) -> MatchResult:
    return make_match("final-et", CLUB_A, CLUB_B, home_goals, away_goals, completed)


def kick(number: int, club: UUID, scored: bool) -> PenaltyKick:
    return PenaltyKick(kick_number=number, club_id=club, scored=scored)


def shootout(first: UUID, results: list[tuple[UUID, bool]]) -> PenaltyShootout:
    kicks = tuple(
        kick(index, club, scored) for index, (club, scored) in enumerate(results, start=1)
    )
    return PenaltyShootout(club_a_id=CLUB_A, club_b_id=CLUB_B, first_kicker_id=first, kicks=kicks)


# --- Orientation and aggregates ---


def test_reversed_second_leg_aggregates_to_a_b_frame() -> None:
    # First A-B 2:1, return B-A 1:0 -> aggregate A-B is 2:2 (not 3:1 home sums).
    decision = decide_tie(tie=make_tie(), first_match=leg1(2, 1), second_match=leg2(1, 0))
    assert (decision.regulation_a, decision.regulation_b) == (2, 2)
    assert (decision.aggregate_a, decision.aggregate_b) == (2, 2)
    assert decision.status == "await_extra_time"
    assert decision.next_segment == "leg2-et"
    assert decision.winner is None


def test_two_leg_winner_after_90_without_extra_time() -> None:
    decision = decide_tie(tie=make_tie(), first_match=leg1(2, 0), second_match=leg2(1, 1))
    # A: 2 + 1 = 3; B: 0 + 1 = 1.
    assert (decision.aggregate_a, decision.aggregate_b) == (3, 1)
    assert decision.status == "decided" and decision.winner == CLUB_A
    assert decision.next_segment is None
    assert (decision.extra_a, decision.extra_b) == (0, 0)


def test_first_leg_draw_waits_for_second_leg() -> None:
    decision = decide_tie(tie=make_tie(), first_match=leg1(1, 1))
    assert decision.status == "await_match"
    assert decision.next_segment == "leg2"
    assert decision.winner is None


def test_no_away_goals_rule_same_aggregate_goes_to_extra_time() -> None:
    # Both legs 2:1 to the hosts -> aggregate 3:3 despite different away splits.
    decision = decide_tie(tie=make_tie(), first_match=leg1(2, 1), second_match=leg2(2, 1))
    assert (decision.regulation_a, decision.regulation_b) == (3, 3)
    assert decision.status == "await_extra_time"
    assert decision.winner is None


def test_winner_after_120_counts_only_extra_goals() -> None:
    tie = make_tie()
    first = leg1(2, 1)
    second = leg2(1, 0)  # regulation 2:2
    extra = leg2_et(1, 0)  # B scores once in extra time
    decision = decide_tie(tie=tie, first_match=first, second_match=second, extra_time=extra)
    assert (decision.regulation_a, decision.regulation_b) == (2, 2)
    assert (decision.extra_a, decision.extra_b) == (0, 1)
    assert (decision.aggregate_a, decision.aggregate_b) == (2, 3)
    assert decision.status == "decided" and decision.winner == CLUB_B


def test_draw_after_completed_extra_time_asks_for_penalties() -> None:
    decision = decide_tie(
        tie=make_tie(), first_match=leg1(1, 0), second_match=leg2(1, 0), extra_time=leg2_et(0, 0)
    )
    assert (decision.aggregate_a, decision.aggregate_b) == (1, 1)
    assert decision.status == "await_penalties"
    assert decision.next_segment == "penalties"
    assert decision.winner is None


def test_extra_time_does_not_recount_regulation() -> None:
    # Regulation 1:1 on aggregate, extra 1:0 to B -> total 1:2, not 2:4.
    decision = decide_tie(
        tie=make_tie(), first_match=leg1(0, 0), second_match=leg2(1, 1), extra_time=leg2_et(1, 0)
    )
    assert (decision.regulation_a, decision.regulation_b) == (1, 1)
    assert (decision.aggregate_a, decision.aggregate_b) == (1, 2)
    assert decision.winner == CLUB_B


def test_incomplete_match_never_decides() -> None:
    assert (
        decide_tie(tie=make_tie(), first_match=leg1(3, 0, completed=False)).status == "await_match"
    )
    incomplete_second = decide_tie(
        tie=make_tie(), first_match=leg1(3, 0), second_match=leg2(0, 5, completed=False)
    )
    assert incomplete_second.status == "await_match"
    assert incomplete_second.next_segment == "leg2"
    assert incomplete_second.winner is None
    incomplete_extra = decide_tie(
        tie=make_tie(),
        first_match=leg1(2, 1),
        second_match=leg2(1, 0),
        extra_time=leg2_et(2, 0, completed=False),
    )
    assert incomplete_extra.status == "await_match"
    assert incomplete_extra.next_segment == "leg2-et"


# --- Single final ---


def test_single_final_decided_after_90() -> None:
    decision = decide_tie(tie=make_tie(tie_format="single"), first_match=final(1, 0))
    assert decision.status == "decided" and decision.winner == CLUB_A
    assert (decision.aggregate_a, decision.aggregate_b) == (1, 0)


def test_single_final_draw_asks_for_extra_time() -> None:
    decision = decide_tie(tie=make_tie(tie_format="single"), first_match=final(1, 1))
    assert decision.status == "await_extra_time"
    assert decision.next_segment == "final-et"


def test_single_final_winner_after_extra_time() -> None:
    decision = decide_tie(
        tie=make_tie(tie_format="single"), first_match=final(1, 1), extra_time=final_et(1, 0)
    )
    assert (decision.aggregate_a, decision.aggregate_b) == (2, 1)
    assert decision.winner == CLUB_A


def test_single_final_extra_draw_asks_for_penalties() -> None:
    decision = decide_tie(
        tie=make_tie(tie_format="single"), first_match=final(0, 0), extra_time=final_et(0, 0)
    )
    assert decision.status == "await_penalties"


# --- Shootouts ---


def test_shootout_early_termination_before_ten_kicks() -> None:
    # A scores three, B misses three -> B cannot catch up after kick 6.
    results = [
        (CLUB_A, True),
        (CLUB_B, False),
        (CLUB_A, True),
        (CLUB_B, False),
        (CLUB_A, True),
        (CLUB_B, False),
    ]
    result = decide_shootout(shootout(CLUB_A, results))
    assert result.status == "decided" and result.winner == CLUB_A
    assert (result.score_a, result.score_b) == (3, 0)
    assert result.next_kicker_id is None and result.next_kick_number is None


def test_shootout_winner_after_ten_without_early_finish() -> None:
    results = [
        (CLUB_A, True),
        (CLUB_B, True),
        (CLUB_A, True),
        (CLUB_B, False),
        (CLUB_A, False),
        (CLUB_B, True),
        (CLUB_A, True),
        (CLUB_B, False),
        (CLUB_A, False),
        (CLUB_B, False),
    ]
    result = decide_shootout(shootout(CLUB_A, results))
    assert (result.score_a, result.score_b) == (3, 2)
    assert result.status == "decided" and result.winner == CLUB_A


def test_sudden_death_needs_equal_kicks_not_first_success() -> None:
    tied_ten = [
        (CLUB_A, True),
        (CLUB_B, True),
        (CLUB_A, False),
        (CLUB_B, False),
        (CLUB_A, True),
        (CLUB_B, True),
        (CLUB_A, False),
        (CLUB_B, False),
        (CLUB_A, True),
        (CLUB_B, True),
    ]
    after_eleven = decide_shootout(shootout(CLUB_A, tied_ten + [(CLUB_A, True)]))
    assert (after_eleven.score_a, after_eleven.score_b) == (4, 3)
    assert after_eleven.status == "awaiting"  # pair incomplete: no instant win
    assert after_eleven.next_kicker_id == CLUB_B
    both_score = decide_shootout(shootout(CLUB_A, tied_ten + [(CLUB_A, True), (CLUB_B, True)]))
    assert both_score.status == "awaiting"
    a_scores_b_misses = decide_shootout(
        shootout(CLUB_A, tied_ten + [(CLUB_A, True), (CLUB_B, False)])
    )
    assert a_scores_b_misses.status == "decided" and a_scores_b_misses.winner == CLUB_A
    # Mirror: first of the pair misses, second scores -> second wins only after the pair.
    b_wins = decide_shootout(shootout(CLUB_A, tied_ten + [(CLUB_A, False), (CLUB_B, True)]))
    assert b_wins.status == "decided" and b_wins.winner == CLUB_B


def test_sudden_death_double_miss_continues() -> None:
    tied_ten = [
        (CLUB_A, True),
        (CLUB_B, True),
        (CLUB_A, True),
        (CLUB_B, True),
        (CLUB_A, False),
        (CLUB_B, False),
        (CLUB_A, False),
        (CLUB_B, False),
        (CLUB_A, True),
        (CLUB_B, True),
    ]
    result = decide_shootout(shootout(CLUB_A, tied_ten + [(CLUB_A, False), (CLUB_B, False)]))
    assert result.status == "awaiting"
    assert (result.score_a, result.score_b) == (3, 3)


def test_penalty_score_never_becomes_regular_goals() -> None:
    base = decide_tie(
        tie=make_tie(), first_match=leg1(0, 0), second_match=leg2(0, 0), extra_time=leg2_et(0, 0)
    )
    assert base.penalty_a == 0 and base.aggregate_a == 0
    decided_shootout = shootout(
        CLUB_A,
        [
            (CLUB_A, True),
            (CLUB_B, False),
            (CLUB_A, True),
            (CLUB_B, False),
            (CLUB_A, True),
            (CLUB_B, False),
        ],
    )
    decision = decide_tie(
        tie=make_tie(),
        first_match=leg1(0, 0),
        second_match=leg2(0, 0),
        extra_time=leg2_et(0, 0),
        shootout=decided_shootout,
    )
    assert decision.status == "decided" and decision.winner == CLUB_A
    assert (decision.penalty_a, decision.penalty_b) == (3, 0)
    assert (decision.aggregate_a, decision.aggregate_b) == (0, 0)
    assert (decision.regulation_a, decision.regulation_b) == (0, 0)


def test_stepwise_replay_is_idempotent_and_recomputes() -> None:
    empty = PenaltyShootout(club_a_id=CLUB_A, club_b_id=CLUB_B, first_kicker_id=CLUB_A, kicks=())
    first = apply_penalty_kick(empty, kick(1, CLUB_A, True))
    replay = apply_penalty_kick(empty, kick(1, CLUB_A, True))
    assert replay == first
    second = apply_penalty_kick(first, kick(2, CLUB_B, False))
    assert decide_shootout(second) == decide_shootout(
        PenaltyShootout(
            club_a_id=CLUB_A, club_b_id=CLUB_B, first_kicker_id=CLUB_A, kicks=second.kicks
        )
    )
    # Full-transcript recomputation matches the stepwise build.
    assert decide_shootout(second).status == "awaiting"
    assert decide_shootout(second).next_kick_number == 3


def test_conflicting_kick_number_and_kicks_after_end_rejected() -> None:
    start = apply_penalty_kick(
        PenaltyShootout(club_a_id=CLUB_A, club_b_id=CLUB_B, first_kicker_id=CLUB_A, kicks=()),
        kick(1, CLUB_A, True),
    )
    with pytest.raises(ValueError):
        apply_penalty_kick(start, kick(1, CLUB_A, False))
    with pytest.raises(ValueError):
        apply_penalty_kick(start, kick(1, CLUB_B, True))
    decided = shootout(
        CLUB_A,
        [
            (CLUB_A, True),
            (CLUB_B, False),
            (CLUB_A, True),
            (CLUB_B, False),
            (CLUB_A, True),
            (CLUB_B, False),
        ],
    )
    assert decide_shootout(decided).status == "decided"
    with pytest.raises(ValueError):
        apply_penalty_kick(decided, kick(7, CLUB_A, True))
    # Bulk transcript with trailing kicks after an early decision is invalid too.
    overlong = shootout(
        CLUB_A,
        [
            (CLUB_A, True),
            (CLUB_B, False),
            (CLUB_A, True),
            (CLUB_B, False),
            (CLUB_A, True),
            (CLUB_B, False),
            (CLUB_A, True),
        ],
    )
    with pytest.raises(ValueError):
        decide_shootout(overlong)
    with pytest.raises(ValueError):
        decide_tie(
            tie=make_tie(),
            first_match=leg1(0, 0),
            second_match=leg2(0, 0),
            extra_time=leg2_et(0, 0),
            shootout=overlong,
        )


# --- Validation ---


def test_invalid_clubs_scores_orientation_and_segments_rejected() -> None:
    with pytest.raises(ValueError):
        make_tie(CLUB_A, CLUB_A)
    with pytest.raises(ValueError):
        make_tie("not-a-uuid", CLUB_B)  # type: ignore[arg-type]
    # Construction allows any UUID pair; orientation is checked in decide_tie.
    foreign = make_match("leg1", CLUB_A, CLUB_C, 1, 0)
    assert {foreign.home_id, foreign.away_id} != {CLUB_A, CLUB_B}
    with pytest.raises(ValueError):
        decide_tie(tie=make_tie(), first_match=make_match("leg1", CLUB_A, CLUB_C, 1, 0))
    for bad in (-1, True, False, 1.5, "2"):
        with pytest.raises(ValueError):
            MatchResult(
                segment="leg1",
                home_id=CLUB_A,
                away_id=CLUB_B,
                home_goals=bad,
                away_goals=0,
                completed=True,
            )  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        MatchResult(
            segment="leg1", home_id=CLUB_A, away_id=CLUB_B, home_goals=1, away_goals=0, completed=1
        )  # type: ignore[arg-type]
    # Swapped home/away on leg1.
    with pytest.raises(ValueError):
        decide_tie(tie=make_tie(), first_match=make_match("leg1", CLUB_B, CLUB_A, 1, 0))
    # Swapped home/away on leg2 (must be B at home).
    with pytest.raises(ValueError):
        decide_tie(
            tie=make_tie(),
            first_match=leg1(1, 0),
            second_match=make_match("leg2", CLUB_A, CLUB_B, 1, 0),
        )
    # Single final must not carry a second leg.
    with pytest.raises(ValueError):
        decide_tie(
            tie=make_tie(tie_format="single"), first_match=final(1, 0), second_match=leg2(0, 0)
        )
    # Wrong segment labels per format.
    with pytest.raises(ValueError):
        decide_tie(tie=make_tie(), first_match=final(1, 0))
    with pytest.raises(ValueError):
        decide_tie(tie=make_tie(tie_format="single"), first_match=leg1(1, 0))


def test_missing_segments_and_unfounded_extra_or_shootout_rejected() -> None:
    with pytest.raises(ValueError):
        decide_tie(tie=make_tie(), second_match=leg2(1, 0))
    with pytest.raises(ValueError):
        decide_tie(tie=make_tie(), first_match=leg1(1, 0), extra_time=leg2_et(0, 0))
    with pytest.raises(ValueError):
        decide_tie(
            tie=make_tie(),
            first_match=leg1(1, 1),
            second_match=leg2(0, 0),
            shootout=shootout(CLUB_A, []),
        )
    # Extra time without a tied aggregate.
    with pytest.raises(ValueError):
        decide_tie(
            tie=make_tie(),
            first_match=leg1(2, 0),
            second_match=leg2(0, 0),
            extra_time=leg2_et(0, 0),
        )
    # Shootout without a tied score after extra time.
    with pytest.raises(ValueError):
        decide_tie(
            tie=make_tie(),
            first_match=leg1(0, 0),
            second_match=leg2(0, 0),
            extra_time=leg2_et(1, 0),
            shootout=shootout(CLUB_A, [(CLUB_A, True)]),
        )
    # Kick order must alternate from the explicit first kicker.
    with pytest.raises(ValueError):
        shootout(CLUB_A, [(CLUB_B, True)])
    with pytest.raises(ValueError):
        PenaltyShootout(club_a_id=CLUB_A, club_b_id=CLUB_B, first_kicker_id=CLUB_C, kicks=())
    with pytest.raises(ValueError):
        PenaltyKick(kick_number=0, club_id=CLUB_A, scored=True)
    with pytest.raises(ValueError):
        PenaltyKick(kick_number=True, club_id=CLUB_A, scored=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        PenaltyKick(kick_number=1, club_id=CLUB_A, scored=1)  # type: ignore[arg-type]
    # Gap in the transcript.
    with pytest.raises(ValueError):
        PenaltyShootout(
            club_a_id=CLUB_A,
            club_b_id=CLUB_B,
            first_kicker_id=CLUB_A,
            kicks=(kick(1, CLUB_A, True), kick(3, CLUB_A, True)),
        )


def test_restored_decision_rejects_contradictory_tallies() -> None:
    decision = decide_tie(tie=make_tie(), first_match=leg1(2, 0), second_match=leg2(1, 1))
    with pytest.raises(ValueError):
        dataclasses.replace(decision, aggregate_a=99)
    with pytest.raises(ValueError):
        dataclasses.replace(decision, winner=CLUB_B)
    with pytest.raises(ValueError):
        dataclasses.replace(decide_shootout(shootout(CLUB_A, [])), score_a=5)


def test_results_are_immutable_and_clock_constants_unchanged() -> None:
    tie = make_tie()
    with pytest.raises(dataclasses.FrozenInstanceError):
        tie.club_a_id = CLUB_C  # type: ignore[misc]
    decision = decide_tie(tie=tie, first_match=leg1(1, 0), second_match=leg2(0, 0))
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.winner = CLUB_B  # type: ignore[misc]
    assert REGULATION_TICKS == 30
    assert EXTRA_TIME_TICKS == 10
    assert KNOCKOUT_POLICY == "knockout-v1"


def _cup_entries() -> list[CupEntry]:
    namespace = UUID("6f2f0f3e-2c3a-4b5e-9f1a-7c8d9e0f1a2b")
    entries: list[CupEntry] = []
    for tier in range(1, 6):
        league_id = uuid5(namespace, f"cup-league:{tier}")
        order = tuple(uuid5(league_id, f"club:{place}") for place in range(1, 11))
        entries.append(CupEntry(league_id=league_id, tier=tier, order=order))
    return entries


def test_knockout_winner_feeds_cup_bracket_validate_winners() -> None:
    season_id = UUID("11111111-1111-1111-1111-111111111111")
    set_id = UUID("22222222-2222-2222-2222-222222222222")
    bracket = build_bracket(
        season_id=season_id, league_set_id=set_id, entries=_cup_entries(), seed=7
    )
    prelim = next(tie for tie in bracket.ties if tie.round_key == "prelim")
    assert prelim.home.club_id is not None and prelim.away.club_id is not None
    club_a = prelim.home.club_id
    club_b = prelim.away.club_id
    tie = KnockoutTie(
        policy_version=KNOCKOUT_POLICY, club_a_id=club_a, club_b_id=club_b, tie_format="two-leg"
    )
    decision = decide_tie(
        tie=tie,
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
            home_goals=0,
            away_goals=1,
            completed=True,
        ),
    )
    assert decision.status == "decided" and decision.winner is not None
    # A decided prelim with direct clubs validates on its own.
    validate_winners(bracket, {prelim.id: decision.winner})
    # The final alone never validates: its participants and ancestors are unknown.
    with pytest.raises(ValueError):
        validate_winners(bracket, {final_tie(bracket).id: decision.winner})
    # An undecided tie must never be recorded as a winner.
    undecided = decide_tie(
        tie=tie,
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
        validate_winners(bracket, {prelim.id: uuid4()})
