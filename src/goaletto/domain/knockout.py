"""Pure knockout tie resolution with extra time and penalties (AND-124).

This module is a deterministic calculator only. It does not touch the ORM,
migrations, bot/application/worker, ledger, portraits or ``en.json``. It does
not assign kickoff times, worker jobs, second-by-second pauses, substitutions,
training, energy, growth, calendar slots or notifications: the durable
orchestration (Codex) owns all of that.

Contract
--------
Explicit frozen :class:`KnockoutTie` for exactly two distinct clubs and one of
two formats:

* ``"two-leg"`` -- home/away legs with exchanged fields plus, when needed,
  extra time as a continuation of the second leg and then penalties;
* ``"single"`` -- one neutral final plus, when needed, extra time and penalties.
  Neutrality travels separately (see ``domain/neutral_final.py``); the score
  orientation stays fixed ``club_a`` at home so aggregates never depend on a
  slot permutation.

Each :class:`MatchResult` pins its ``segment`` (``leg1`` / ``leg2`` /
``leg2-et`` / ``final`` / ``final-et``), exact home/away club IDs, integer
goals and an explicit ``completed`` flag. An incomplete or abandoned match
never decides a tie: :func:`decide_tie` returns an awaiting status instead.

Orientation is reversed on the second leg: a first leg ``A-B 2:1`` followed by
a return ``B-A 1:0`` aggregates to ``A-B 2:2``. Home goals of the two legs are
never summed mechanically. There is no away-goals rule. A drawn first leg is
allowed and never triggers extra time by itself.

Progression (UEFA-style, without hidden rules):

* after completed regulation with a non-tied aggregate (two legs) or a
  non-draw (single final) the tie is decided;
* after completed regulation with a tied aggregate / drawn final,
  :func:`decide_tie` asks for extra time (``await_extra_time``);
* extra-time goals only add their own goals on top of regulation; when extra
  time is completed and the total differs, the tie is decided;
* when completed extra time is still tied, :func:`decide_tie` asks for
  penalties (``await_penalties``); a decided shootout decides the tie.
* extra time or a shootout supplied without the required tied basis raises
  ``ValueError`` (``ET without basis`` / ``shootout without basis``), as do
  missing segments (second leg without first, extra time without completed
  regulation, shootout without completed extra time).

Penalties are pure input, never simulated: :class:`PenaltyShootout` pins the
explicit ``first_kicker_id`` and an immutable transcript of
:class:`PenaltyKick` rows (explicit 1-based ``kick_number``, club, ``scored``).
Order strictly alternates starting from the first kicker. The first five kicks
per club allow early termination whenever the trailing side can no longer
catch up; beyond 5-5 the shootout continues in sudden-death pairs with no
length limit and no forced random winner. A winner in sudden death is declared
only after an equal number of kicks (both sides of the pair), never right
after the first successful kick of a pair. Penalty goals are kept strictly
separate (``penalty_a`` / ``penalty_b``) and never added to regulation,
extra-time or aggregate goals, player tallies or tables.

Stepwise intake :func:`apply_penalty_kick` takes the same explicit kick
number: re-applying the identical recorded kick returns the unchanged
transcript (idempotent replay), while a conflicting payload under the same
number, a gap, a wrong kicker, or any new kick after the shootout is decided
raises ``ValueError``. Recomputing via :func:`decide_shootout` over the full
immutable transcript always replays the same result; transcripts with kicks
recorded after the decision point are rejected.

Clock note (informational, not scheduled here): regulation stays 30 ticks of
3 game minutes with a 3-minute halftime (33 real minutes, no added delays);
extra time adds 10 ticks (5+5). This module assigns no pauses, live-command
deadlines or substitution windows. Open integration points owned elsewhere:
extra-time pause placement, live-command deadlines during extra time and
shootouts, player availability for extra time, penalty-taker selection and
save probabilities, durable phases/checkpoints/jobs, engine minutes, energy
settlement, development growth, calendar reservation, outbox notices and
PostgreSQL retries/idempotency. See ``docs/knockout-domain.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

KNOCKOUT_POLICY = "knockout-v1"
REGULATION_TICKS = 30
EXTRA_TIME_TICKS = 10
SHOOTOUT_INITIAL_KICKS = 5

TieFormat = Literal["two-leg", "single"]
MatchSegment = Literal["leg1", "leg2", "leg2-et", "final", "final-et"]
TieStatus = Literal["await_match", "await_extra_time", "await_penalties", "decided"]
ShootoutStatus = Literal["awaiting", "decided"]

_NEXT_SEGMENTS = ("leg1", "leg2", "leg2-et", "final", "final-et", "penalties")
_MATCH_SEGMENTS = ("leg1", "leg2", "leg2-et", "final", "final-et")

__all__ = [
    "KNOCKOUT_POLICY",
    "REGULATION_TICKS",
    "EXTRA_TIME_TICKS",
    "SHOOTOUT_INITIAL_KICKS",
    "KnockoutTie",
    "MatchResult",
    "PenaltyKick",
    "PenaltyShootout",
    "ShootoutResult",
    "TieDecision",
    "decide_shootout",
    "apply_penalty_kick",
    "decide_tie",
]


def _check_club_id(value: UUID, name: str) -> None:
    if not isinstance(value, UUID):
        raise ValueError(f"{name} must be UUID")


def _check_score(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative int")


def _check_kick_number(value: int) -> None:
    if type(value) is not int or value < 1:
        raise ValueError("Kick number must be a positive int")


def _check_flag(value: bool, name: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{name} must be bool")


def _other_club(club_a_id: UUID, club_b_id: UUID, club_id: UUID) -> UUID:
    if club_id == club_a_id:
        return club_b_id
    if club_id == club_b_id:
        return club_a_id
    raise ValueError("Penalty club is foreign to this shootout")


def _expected_kicker(first_kicker_id: UUID, other_id: UUID, kick_number: int) -> UUID:
    if kick_number % 2 == 1:
        return first_kicker_id
    return other_id


@dataclass(frozen=True)
class KnockoutTie:
    """Two clubs and the knockout format (no scores inside)."""

    policy_version: str
    club_a_id: UUID
    club_b_id: UUID
    tie_format: TieFormat

    def __post_init__(self) -> None:
        if self.policy_version != KNOCKOUT_POLICY:
            raise ValueError("Invalid knockout policy")
        _check_club_id(self.club_a_id, "Club A id")
        _check_club_id(self.club_b_id, "Club B id")
        if self.club_a_id == self.club_b_id:
            raise ValueError("Knockout tie needs two distinct clubs")
        if self.tie_format not in ("two-leg", "single"):
            raise ValueError("Tie format must be 'two-leg' or 'single'")


@dataclass(frozen=True)
class MatchResult:
    """One immutable segment result with an explicit completion flag."""

    segment: MatchSegment
    home_id: UUID
    away_id: UUID
    home_goals: int
    away_goals: int
    completed: bool

    def __post_init__(self) -> None:
        if self.segment not in _MATCH_SEGMENTS:
            raise ValueError("Unknown match segment")
        _check_club_id(self.home_id, "Home id")
        _check_club_id(self.away_id, "Away id")
        if self.home_id == self.away_id:
            raise ValueError("A match needs two distinct clubs")
        _check_score(self.home_goals, "Home goals")
        _check_score(self.away_goals, "Away goals")
        _check_flag(self.completed, "Completed flag")

    def goals_for(self, club_a_id: UUID, club_b_id: UUID) -> tuple[int, int]:
        """Return ``(goals_a, goals_b)`` oriented to club A/B."""
        if {self.home_id, self.away_id} != {club_a_id, club_b_id}:
            raise ValueError("Match clubs differ from the tie clubs")
        if self.home_id == club_a_id:
            return (self.home_goals, self.away_goals)
        return (self.away_goals, self.home_goals)


@dataclass(frozen=True)
class PenaltyKick:
    """One immutable penalty attempt with an explicit 1-based number."""

    kick_number: int
    club_id: UUID
    scored: bool

    def __post_init__(self) -> None:
        _check_kick_number(self.kick_number)
        _check_club_id(self.club_id, "Kick club id")
        _check_flag(self.scored, "Scored flag")


@dataclass(frozen=True)
class PenaltyShootout:
    """Immutable shootout transcript with an explicit first kicker."""

    club_a_id: UUID
    club_b_id: UUID
    first_kicker_id: UUID
    kicks: tuple[PenaltyKick, ...]

    def __post_init__(self) -> None:
        _check_club_id(self.club_a_id, "Club A id")
        _check_club_id(self.club_b_id, "Club B id")
        if self.club_a_id == self.club_b_id:
            raise ValueError("Shootout needs two distinct clubs")
        _check_club_id(self.first_kicker_id, "First kicker id")
        if self.first_kicker_id not in (self.club_a_id, self.club_b_id):
            raise ValueError("First kicker is foreign to this shootout")
        if type(self.kicks) is not tuple:
            raise ValueError("Kicks must be a tuple of PenaltyKick rows")
        for kick in self.kicks:
            if not isinstance(kick, PenaltyKick):
                raise ValueError("Kicks must be PenaltyKick rows")
        other_id = _other_club(self.club_a_id, self.club_b_id, self.first_kicker_id)
        for index, kick in enumerate(self.kicks, start=1):
            if kick.kick_number != index:
                raise ValueError("Kick numbers must run 1..N without gaps")
            if kick.club_id != _expected_kicker(self.first_kicker_id, other_id, index):
                raise ValueError("Penalty kick order must strictly alternate")


def _eval_shootout(
    club_a_id: UUID,
    club_b_id: UUID,
    first_kicker_id: UUID,
    kicks: tuple[PenaltyKick, ...],
) -> tuple[int, int, int, int, ShootoutStatus, UUID | None, UUID | None, int | None]:
    """Evaluate a validated transcript; reject kicks recorded past the end."""
    other_id = _other_club(club_a_id, club_b_id, first_kicker_id)
    for index, kick in enumerate(kicks, start=1):
        if kick.kick_number != index:
            raise ValueError("Kick numbers must run 1..N without gaps")
        if kick.club_id != _expected_kicker(first_kicker_id, other_id, index):
            raise ValueError("Penalty kick order must strictly alternate")
    score_a = sum(1 for kick in kicks if kick.club_id == club_a_id and kick.scored)
    score_b = sum(1 for kick in kicks if kick.club_id == club_b_id and kick.scored)
    taken_a = sum(1 for kick in kicks if kick.club_id == club_a_id)
    taken_b = sum(1 for kick in kicks if kick.club_id == club_b_id)

    def winner_at(
        prefix_taken_a: int, prefix_taken_b: int, prefix_a: int, prefix_b: int, total: int
    ) -> UUID | None:
        if total <= 2 * SHOOTOUT_INITIAL_KICKS:
            remaining_a = SHOOTOUT_INITIAL_KICKS - prefix_taken_a
            remaining_b = SHOOTOUT_INITIAL_KICKS - prefix_taken_b
            if prefix_a > prefix_b + remaining_b:
                return club_a_id
            if prefix_b > prefix_a + remaining_a:
                return club_b_id
            if (
                prefix_taken_a == SHOOTOUT_INITIAL_KICKS
                and prefix_taken_b == SHOOTOUT_INITIAL_KICKS
            ):
                if prefix_a > prefix_b:
                    return club_a_id
                if prefix_b > prefix_a:
                    return club_b_id
            return None
        if total % 2 == 0:
            if prefix_a > prefix_b:
                return club_a_id
            if prefix_b > prefix_a:
                return club_b_id
        return None

    running_a = 0
    running_b = 0
    count_a = 0
    count_b = 0
    for kick in kicks:
        if kick.club_id == club_a_id:
            count_a += 1
            running_a += 1 if kick.scored else 0
        else:
            count_b += 1
            running_b += 1 if kick.scored else 0
        decided = winner_at(count_a, count_b, running_a, running_b, count_a + count_b)
        if decided is not None and count_a + count_b < len(kicks):
            raise ValueError("Shootout records kicks after its completion")
    final = winner_at(taken_a, taken_b, score_a, score_b, len(kicks))
    if final is not None:
        return (score_a, score_b, taken_a, taken_b, "decided", final, None, None)
    next_number = len(kicks) + 1
    return (
        score_a,
        score_b,
        taken_a,
        taken_b,
        "awaiting",
        None,
        _expected_kicker(first_kicker_id, other_id, next_number),
        next_number,
    )


@dataclass(frozen=True)
class ShootoutResult:
    """Immutable shootout evaluation (scores pinned, goals kept separate)."""

    policy_version: str
    club_a_id: UUID
    club_b_id: UUID
    first_kicker_id: UUID
    kicks: tuple[PenaltyKick, ...]
    score_a: int
    score_b: int
    taken_a: int
    taken_b: int
    status: ShootoutStatus
    winner: UUID | None
    next_kicker_id: UUID | None
    next_kick_number: int | None

    def __post_init__(self) -> None:
        if self.policy_version != KNOCKOUT_POLICY:
            raise ValueError("Invalid knockout policy")
        _check_club_id(self.club_a_id, "Club A id")
        _check_club_id(self.club_b_id, "Club B id")
        if self.club_a_id == self.club_b_id:
            raise ValueError("Shootout needs two distinct clubs")
        if self.status not in ("awaiting", "decided"):
            raise ValueError("Invalid shootout status")
        PenaltyShootout(self.club_a_id, self.club_b_id, self.first_kicker_id, self.kicks)
        expected = _eval_shootout(self.club_a_id, self.club_b_id, self.first_kicker_id, self.kicks)
        (score_a, score_b, taken_a, taken_b, status, winner, next_kicker, next_number) = expected
        if (
            self.score_a != score_a
            or self.score_b != score_b
            or self.taken_a != taken_a
            or self.taken_b != taken_b
            or self.status != status
            or self.winner != winner
            or self.next_kicker_id != next_kicker
            or self.next_kick_number != next_number
        ):
            raise ValueError("Shootout result differs from its transcript")
        _check_score(self.score_a, "Penalty score A")
        _check_score(self.score_b, "Penalty score B")
        if type(self.taken_a) is not int or self.taken_a < 0:
            raise ValueError("Taken kicks must be non-negative ints")
        if type(self.taken_b) is not int or self.taken_b < 0:
            raise ValueError("Taken kicks must be non-negative ints")
        if self.status == "decided":
            if self.winner not in (self.club_a_id, self.club_b_id):
                raise ValueError("Shootout winner is foreign to this shootout")
            if self.next_kicker_id is not None or self.next_kick_number is not None:
                raise ValueError("Decided shootout has no next kick")
        else:
            if self.winner is not None:
                raise ValueError("Undecided shootout has no winner")
            if self.next_kicker_id not in (self.club_a_id, self.club_b_id):
                raise ValueError("Next kicker is foreign to this shootout")
            if (
                type(self.next_kick_number) is not int
                or self.next_kick_number != len(self.kicks) + 1
            ):
                raise ValueError("Next kick number must continue the transcript")


def decide_shootout(shootout: PenaltyShootout) -> ShootoutResult:
    """Evaluate a full immutable penalty transcript.

    :param shootout: frozen clubs, first kicker and ordered kicks 1..N.
    :returns: immutable result with separate penalty scores and either the
        winner or the next expected kicker/number.
    :raises ValueError: on foreign clubs, gaps, order violations or kicks
        recorded after the shootout was already decided.
    """
    if not isinstance(shootout, PenaltyShootout):
        raise ValueError("Shootout must be a PenaltyShootout")
    (score_a, score_b, taken_a, taken_b, status, winner, next_kicker, next_number) = _eval_shootout(
        shootout.club_a_id, shootout.club_b_id, shootout.first_kicker_id, shootout.kicks
    )
    return ShootoutResult(
        policy_version=KNOCKOUT_POLICY,
        club_a_id=shootout.club_a_id,
        club_b_id=shootout.club_b_id,
        first_kicker_id=shootout.first_kicker_id,
        kicks=shootout.kicks,
        score_a=score_a,
        score_b=score_b,
        taken_a=taken_a,
        taken_b=taken_b,
        status=status,
        winner=winner,
        next_kicker_id=next_kicker,
        next_kick_number=next_number,
    )


def apply_penalty_kick(shootout: PenaltyShootout, kick: PenaltyKick) -> PenaltyShootout:
    """Append one explicitly numbered kick with idempotent replay.

    Re-applying the identical recorded kick returns the unchanged transcript;
    a conflicting payload under the same number, a gap, a wrong kicker, or any
    new kick after the shootout is decided raises ``ValueError``.
    """
    if not isinstance(shootout, PenaltyShootout):
        raise ValueError("Shootout must be a PenaltyShootout")
    if not isinstance(kick, PenaltyKick):
        raise ValueError("Kick must be a PenaltyKick")
    if kick.club_id not in (shootout.club_a_id, shootout.club_b_id):
        raise ValueError("Penalty club is foreign to this shootout")
    current = decide_shootout(shootout)
    existing = {row.kick_number: row for row in shootout.kicks}
    if kick.kick_number in existing:
        if existing[kick.kick_number] == kick:
            return shootout
        raise ValueError("Conflicting payload under a recorded kick number")
    if kick.kick_number != len(shootout.kicks) + 1:
        raise ValueError("Kick numbers must run 1..N without gaps")
    if current.status == "decided":
        raise ValueError("Shootout is already decided")
    if kick.club_id != current.next_kicker_id or kick.kick_number != current.next_kick_number:
        raise ValueError("Penalty kick breaks the alternating order")
    return PenaltyShootout(
        club_a_id=shootout.club_a_id,
        club_b_id=shootout.club_b_id,
        first_kicker_id=shootout.first_kicker_id,
        kicks=shootout.kicks + (kick,),
    )


def _require_orientation(tie: KnockoutTie, match: MatchResult, *, expected_home: UUID) -> None:
    if match.home_id != expected_home:
        raise ValueError("Match home/away orientation differs from the tie")
    expected_away = tie.club_b_id if expected_home == tie.club_a_id else tie.club_a_id
    if match.away_id != expected_away:
        raise ValueError("Match home/away orientation differs from the tie")


def _derive_tie(
    tie: KnockoutTie,
    first_match: MatchResult | None,
    second_match: MatchResult | None,
    extra_time: MatchResult | None,
    shootout: PenaltyShootout | None,
) -> tuple[int, int, int, int, int, int, int, int, TieStatus, UUID | None, str | None]:
    """Validate inputs and compute regulation/extra/aggregate/penalty + routing."""
    if not isinstance(tie, KnockoutTie):
        raise ValueError("Tie must be a KnockoutTie")
    for row, name in (
        (first_match, "First match"),
        (second_match, "Second match"),
        (extra_time, "Extra time"),
    ):
        if row is not None and not isinstance(row, MatchResult):
            raise ValueError(f"{name} must be a MatchResult or None")
    if shootout is not None and not isinstance(shootout, PenaltyShootout):
        raise ValueError("Shootout must be a PenaltyShootout or None")

    if tie.tie_format == "two-leg":
        if first_match is not None and first_match.segment != "leg1":
            raise ValueError("Two-leg first match must use segment 'leg1'")
        if second_match is not None and second_match.segment != "leg2":
            raise ValueError("Two-leg second match must use segment 'leg2'")
        if extra_time is not None and extra_time.segment != "leg2-et":
            raise ValueError("Two-leg extra time must use segment 'leg2-et'")
        if second_match is not None and first_match is None:
            raise ValueError("Second leg without the first leg")
        if second_match is not None and first_match is not None and not first_match.completed:
            raise ValueError("Second leg before completed first leg")
        if extra_time is not None and (first_match is None or second_match is None):
            raise ValueError("Extra time without completed regulation legs")
        if first_match is not None:
            _require_orientation(tie, first_match, expected_home=tie.club_a_id)
        if second_match is not None:
            _require_orientation(tie, second_match, expected_home=tie.club_b_id)
        if extra_time is not None:
            _require_orientation(tie, extra_time, expected_home=tie.club_b_id)
    else:
        if second_match is not None:
            raise ValueError("Single final has no second leg")
        if first_match is not None and first_match.segment != "final":
            raise ValueError("Single final regulation must use segment 'final'")
        if extra_time is not None and extra_time.segment != "final-et":
            raise ValueError("Single final extra time must use segment 'final-et'")
        if extra_time is not None and first_match is None:
            raise ValueError("Extra time without the final regulation")
        if first_match is not None:
            _require_orientation(tie, first_match, expected_home=tie.club_a_id)
        if extra_time is not None:
            _require_orientation(tie, extra_time, expected_home=tie.club_a_id)

    if shootout is not None:
        if shootout.club_a_id != tie.club_a_id or shootout.club_b_id != tie.club_b_id:
            raise ValueError("Shootout clubs differ from the tie clubs")

    await_first: str = "leg1" if tie.tie_format == "two-leg" else "final"
    await_second: str = "leg2" if tie.tie_format == "two-leg" else "final"
    await_extra: str = "leg2-et" if tie.tie_format == "two-leg" else "final-et"

    def decided(
        reg_a: int,
        reg_b: int,
        ext_a: int,
        ext_b: int,
        pen_a: int,
        pen_b: int,
        winner: UUID,
    ) -> tuple[int, int, int, int, int, int, int, int, TieStatus, UUID | None, str | None]:
        return (
            reg_a,
            reg_b,
            ext_a,
            ext_b,
            reg_a + ext_a,
            reg_b + ext_b,
            pen_a,
            pen_b,
            "decided",
            winner,
            None,
        )

    def waiting(
        status: TieStatus,
        nxt: str,
        reg_a: int,
        reg_b: int,
        ext_a: int,
        ext_b: int,
        pen_a: int,
        pen_b: int,
    ) -> tuple[int, int, int, int, int, int, int, int, TieStatus, UUID | None, str | None]:
        return (
            reg_a,
            reg_b,
            ext_a,
            ext_b,
            reg_a + ext_a,
            reg_b + ext_b,
            pen_a,
            pen_b,
            status,
            None,
            nxt,
        )

    if tie.tie_format == "two-leg":
        assert await_first == "leg1"
        if first_match is None or not first_match.completed:
            if extra_time is not None:
                raise ValueError("Extra time without completed regulation legs")
            if shootout is not None:
                raise ValueError("Shootout without completed extra time")
            return waiting("await_match", "leg1", 0, 0, 0, 0, 0, 0)
        first_a, first_b = first_match.goals_for(tie.club_a_id, tie.club_b_id)
        if second_match is None or not second_match.completed:
            if extra_time is not None:
                raise ValueError("Extra time without completed regulation legs")
            if shootout is not None:
                raise ValueError("Shootout without completed extra time")
            return waiting("await_match", "leg2", first_a, first_b, 0, 0, 0, 0)
        second_a, second_b = second_match.goals_for(tie.club_a_id, tie.club_b_id)
        reg_a = first_a + second_a
        reg_b = first_b + second_b
        if reg_a != reg_b:
            if extra_time is not None:
                raise ValueError("Extra time without a tied aggregate")
            if shootout is not None:
                raise ValueError("Shootout without completed extra time")
            return decided(
                reg_a, reg_b, 0, 0, 0, 0, tie.club_a_id if reg_a > reg_b else tie.club_b_id
            )
        if extra_time is None:
            if shootout is not None:
                raise ValueError("Shootout without completed extra time")
            return waiting("await_extra_time", await_extra, reg_a, reg_b, 0, 0, 0, 0)
        if not extra_time.completed:
            if shootout is not None:
                raise ValueError("Shootout without completed extra time")
            return waiting("await_match", await_extra, reg_a, reg_b, 0, 0, 0, 0)
        ext_a, ext_b = extra_time.goals_for(tie.club_a_id, tie.club_b_id)
        if reg_a + ext_a != reg_b + ext_b:
            if shootout is not None:
                raise ValueError("Shootout without a tied score after extra time")
            winner = tie.club_a_id if reg_a + ext_a > reg_b + ext_b else tie.club_b_id
            return decided(reg_a, reg_b, ext_a, ext_b, 0, 0, winner)
        if shootout is None:
            return waiting("await_penalties", "penalties", reg_a, reg_b, ext_a, ext_b, 0, 0)
        result = decide_shootout(shootout)
        if result.status == "decided":
            assert result.winner is not None
            return decided(
                reg_a, reg_b, ext_a, ext_b, result.score_a, result.score_b, result.winner
            )
        return waiting(
            "await_penalties",
            "penalties",
            reg_a,
            reg_b,
            ext_a,
            ext_b,
            result.score_a,
            result.score_b,
        )

    # Single final.
    if first_match is None or not first_match.completed:
        if extra_time is not None:
            raise ValueError("Extra time without the final regulation")
        if shootout is not None:
            raise ValueError("Shootout without completed extra time")
        return waiting("await_match", await_second, 0, 0, 0, 0, 0, 0)
    reg_a, reg_b = first_match.goals_for(tie.club_a_id, tie.club_b_id)
    if reg_a != reg_b:
        if extra_time is not None:
            raise ValueError("Extra time without a drawn final")
        if shootout is not None:
            raise ValueError("Shootout without completed extra time")
        return decided(reg_a, reg_b, 0, 0, 0, 0, tie.club_a_id if reg_a > reg_b else tie.club_b_id)
    if extra_time is None:
        if shootout is not None:
            raise ValueError("Shootout without completed extra time")
        return waiting("await_extra_time", await_extra, reg_a, reg_b, 0, 0, 0, 0)
    if not extra_time.completed:
        if shootout is not None:
            raise ValueError("Shootout without completed extra time")
        return waiting("await_match", await_extra, reg_a, reg_b, 0, 0, 0, 0)
    ext_a, ext_b = extra_time.goals_for(tie.club_a_id, tie.club_b_id)
    if reg_a + ext_a != reg_b + ext_b:
        if shootout is not None:
            raise ValueError("Shootout without a tied score after extra time")
        winner = tie.club_a_id if reg_a + ext_a > reg_b + ext_b else tie.club_b_id
        return decided(reg_a, reg_b, ext_a, ext_b, 0, 0, winner)
    if shootout is None:
        return waiting("await_penalties", "penalties", reg_a, reg_b, ext_a, ext_b, 0, 0)
    result = decide_shootout(shootout)
    if result.status == "decided":
        assert result.winner is not None
        return decided(reg_a, reg_b, ext_a, ext_b, result.score_a, result.score_b, result.winner)
    return waiting(
        "await_penalties", "penalties", reg_a, reg_b, ext_a, ext_b, result.score_a, result.score_b
    )


@dataclass(frozen=True)
class TieDecision:
    """Immutable tie evaluation with separated regulation/ET/aggregate/penalties."""

    policy_version: str
    club_a_id: UUID
    club_b_id: UUID
    tie_format: TieFormat
    status: TieStatus
    winner: UUID | None
    next_segment: str | None
    regulation_a: int
    regulation_b: int
    extra_a: int
    extra_b: int
    aggregate_a: int
    aggregate_b: int
    penalty_a: int
    penalty_b: int
    first_match: MatchResult | None
    second_match: MatchResult | None
    extra_time: MatchResult | None
    shootout: PenaltyShootout | None

    def __post_init__(self) -> None:
        if self.policy_version != KNOCKOUT_POLICY:
            raise ValueError("Invalid knockout policy")
        _check_club_id(self.club_a_id, "Club A id")
        _check_club_id(self.club_b_id, "Club B id")
        if self.club_a_id == self.club_b_id:
            raise ValueError("Knockout tie needs two distinct clubs")
        if self.tie_format not in ("two-leg", "single"):
            raise ValueError("Tie format must be 'two-leg' or 'single'")
        if self.status not in ("await_match", "await_extra_time", "await_penalties", "decided"):
            raise ValueError("Invalid tie status")
        if self.status == "decided":
            if self.winner not in (self.club_a_id, self.club_b_id):
                raise ValueError("Tie winner is foreign to this tie")
            if self.next_segment is not None:
                raise ValueError("Decided tie has no next segment")
        else:
            if self.winner is not None:
                raise ValueError("Undecided tie has no winner")
            if self.next_segment not in _NEXT_SEGMENTS:
                raise ValueError("Unknown next segment")
            if self.status == "await_extra_time" and self.next_segment not in (
                "leg2-et",
                "final-et",
            ):
                raise ValueError("Extra-time routing needs an extra-time segment")
            if self.status == "await_penalties" and self.next_segment != "penalties":
                raise ValueError("Penalty routing needs the penalties segment")
        for name in (
            "regulation_a",
            "regulation_b",
            "extra_a",
            "extra_b",
            "aggregate_a",
            "aggregate_b",
            "penalty_a",
            "penalty_b",
        ):
            _check_score(getattr(self, name), name)
        tie = KnockoutTie(
            policy_version=self.policy_version,
            club_a_id=self.club_a_id,
            club_b_id=self.club_b_id,
            tie_format=self.tie_format,
        )
        expected = _derive_tie(
            tie, self.first_match, self.second_match, self.extra_time, self.shootout
        )
        (
            reg_a,
            reg_b,
            ext_a,
            ext_b,
            agg_a,
            agg_b,
            pen_a,
            pen_b,
            status,
            winner,
            nxt,
        ) = expected
        if (
            self.regulation_a != reg_a
            or self.regulation_b != reg_b
            or self.extra_a != ext_a
            or self.extra_b != ext_b
            or self.aggregate_a != agg_a
            or self.aggregate_b != agg_b
            or self.penalty_a != pen_a
            or self.penalty_b != pen_b
            or self.status != status
            or self.winner != winner
            or self.next_segment != nxt
        ):
            raise ValueError("Tie decision differs from its pinned segments")


def decide_tie(
    *,
    tie: KnockoutTie,
    first_match: MatchResult | None = None,
    second_match: MatchResult | None = None,
    extra_time: MatchResult | None = None,
    shootout: PenaltyShootout | None = None,
) -> TieDecision:
    """Resolve one tie from explicitly pinned immutable segments.

    :param tie: the two clubs and the ``two-leg`` / ``single`` format.
    :param first_match: ``leg1`` (two legs) or ``final`` (single) regulation.
    :param second_match: ``leg2`` regulation (two legs only; always ``None``
        for a single final).
    :param extra_time: ``leg2-et`` / ``final-et`` goals only (regulation goals
        are never recounted inside this segment).
    :param shootout: full or partial immutable penalty transcript; penalty
        goals never enter regulation/extra/aggregate tallies.
    :returns: immutable decision with ``await_match`` / ``await_extra_time`` /
        ``await_penalties`` / ``decided`` routing, the next segment (or
        ``None`` when decided), separated regulation/extra/aggregate/penalty
        tallies and the single winner (or ``None`` while awaiting).
    :raises ValueError: on foreign/duplicated clubs, non-int or negative
        scores, wrong home/away orientation, missing segments, extra time or
        shootouts without the required tied basis, contradictory kick order,
        or kicks recorded after the shootout was decided.
    """
    if not isinstance(tie, KnockoutTie):
        raise ValueError("Tie must be a KnockoutTie")
    (reg_a, reg_b, ext_a, ext_b, agg_a, agg_b, pen_a, pen_b, status, winner, nxt) = _derive_tie(
        tie, first_match, second_match, extra_time, shootout
    )
    return TieDecision(
        policy_version=KNOCKOUT_POLICY,
        club_a_id=tie.club_a_id,
        club_b_id=tie.club_b_id,
        tie_format=tie.tie_format,
        status=status,
        winner=winner,
        next_segment=nxt,
        regulation_a=reg_a,
        regulation_b=reg_b,
        extra_a=ext_a,
        extra_b=ext_b,
        aggregate_a=agg_a,
        aggregate_b=agg_b,
        penalty_a=pen_a,
        penalty_b=pen_b,
        first_match=first_match,
        second_match=second_match,
        extra_time=extra_time,
        shootout=shootout,
    )
