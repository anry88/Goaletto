"""Pure champions-cup knockout bracket inside one ready group (AND-126).

Contract
--------
Independent computational subpackage, not the whole tournament. Pure Python
with immutable inputs/results and an explicit seed: no DB, network, clocks,
Telegram, worker, aiogram, SQLAlchemy or unseeded RNG.

Input: one explicitly frozen
:class:`goaletto.domain.champions_groups.ChampionsGroup` (qualification is
already pinned there as top-eight places with ``qualifying_tier``,
``qualifying_season_id`` and ``qualifying_place``), one orchestration-chosen
``tournament_id`` UUID for this inter-set tournament and an explicit ``seed``.
The ``tournament_id`` namespaces stable tie IDs; an arbitrary season of one
participant must never be reused as the season of the whole tournament. The
module never reselects top-eight clubs, never rewrites the qualifying
tier/season from live tables and never invents a past table for the first
season: missing qualification is ``ValueError``, surfaced as "no bracket".

Format: ordinary single-elimination knockout inside one group. ``N`` is one
of 8/16/32/64/128/256/512. Every club starts in round 1, no staged entry of
the 50-club cup and no byes. The first-round draw is seeded and independent
of input order: the base club order is sorted by hex and only then shuffled
with ``stable_seed``. Later winners advance through fixed winner-slot links,
never through a fresh random draw per result or retry. Rebuilding with the
same inputs replays the same bracket; the exact pairs plus the policy are
suitable for a future persisted snapshot.

Schedule (structural champions cycles; ``phase_day`` is day 2 of the
four-day cycle; exact kickoff instants and worker jobs are owned by Codex
inside the ten-day horizon and are never assigned here):

* round ``r < R`` (``R = log2(N)``) holds ``N / 2**r`` ties with two legs and
  exchanged hosts in cycles ``2r-1`` and ``2r``;
* the final (round ``R``) is one neutral match in cycle ``2R-1``;
* totals: ``N-1`` ties, ``2N-3`` matches, ``2R-1`` match days (5..17);
* ``champions_cycles`` are ``1..D`` (``D = 2R-1``), ``free_cycles`` are
  ``D+1..18``; cycle 18 is always free with no fictitious matches.

Slots reuse :class:`goaletto.domain.cup_bracket.CupSlot` (``club`` or
``winner(tie)``) and its constructors; ``CupTie``/``CupLeg`` stay limited to
the fixed rounds of the other cup and are not changed here. No second match
engine is built: aggregate/extra-time/penalties come from the shared
``domain.knockout`` runtime (AND-124), minutes/energy/progress, arena/ledger
and bootstrap stay outside.

Progression (draws of single matches are never winners; only decided
``domain.knockout`` tie winners may be recorded):

* :func:`resolve_slot` returns the concrete club when known;
* :func:`possible_clubs` / :func:`possible_tie_clubs` return conditional
  club sets: after an elimination the loser branch disappears while the
  other branch is preserved;
* :func:`validate_winners` rejects unknown ties, foreign clubs, winners
  outside the tie subtree, incompatible downstream winners, duplicate
  winners on unrelated branches and unresolved feeders. All public readers
  validate the whole winners mapping before using it. The final alone never
  decides the champion: its feeders and their ancestors must be present.
* :func:`champion` returns the final winner when decided.

A restored graph is validated as strictly as a fresh draw: frozen group
clubs enter each exactly once, no foreign/lost clubs, no cycles, no feeder
reuse in two places, no identical opponents, no wrong rounds/days and exactly
one neutral final. Stable IDs/replay never depend on wall-clock or dict
order.

This bracket grants no growth itself and introduces no cup-growth ban. All
real matches (league, cup, champions, friendlies) affect form and
development by actual minutes; friendlies grant less than official games
with a multiplier still to be decided. Shared caps/potential still apply;
Codex connects this bracket under AND-130 in the common runtime.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from random import Random
from uuid import UUID, uuid5

from goaletto.domain.champions_groups import ChampionsGroup
from goaletto.domain.cup_bracket import CupSlot, club_slot, winner_slot
from goaletto.domain.world import stable_seed

CHAMPIONS_BRACKET_POLICY = "champions-bracket-v1"
CHAMPIONS_BRACKET_NAMESPACE = UUID("8b9c2d4e-5f6a-4b7c-8d9e-0f1a2b3c4d5e")
TOTAL_CHAMPIONS_CYCLES = 18
RESERVE_CHAMPIONS_CYCLE = 18
CHAMPIONS_PHASE_DAY = 2

ALLOWED_PARTICIPANT_COUNTS: tuple[int, ...] = (8, 16, 32, 64, 128, 256, 512)

__all__ = [
    "CHAMPIONS_BRACKET_POLICY",
    "CHAMPIONS_BRACKET_NAMESPACE",
    "TOTAL_CHAMPIONS_CYCLES",
    "RESERVE_CHAMPIONS_CYCLE",
    "CHAMPIONS_PHASE_DAY",
    "ALLOWED_PARTICIPANT_COUNTS",
    "ChampionsTie",
    "ChampionsLeg",
    "ChampionsBracket",
    "tie_id_for",
    "rounds_for",
    "match_days_for",
    "champions_cycles_for",
    "free_cycles_for",
    "build_champions_bracket",
    "validate_against_group",
    "tie_by_id",
    "final_tie",
    "legs_of",
    "all_matches",
    "resolve_slot",
    "possible_clubs",
    "possible_tie_clubs",
    "validate_winners",
    "champion",
]


def _check_seed(seed: int) -> None:
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("Seed must fit nonnegative BIGINT")


def _check_tournament_id(tournament_id: UUID) -> None:
    if not isinstance(tournament_id, UUID):
        raise ValueError("Tournament id must be UUID")


def _check_group_id(group_id: UUID) -> None:
    if not isinstance(group_id, UUID):
        raise ValueError("Group id must be UUID")


@dataclass(frozen=True)
class ChampionsTie:
    """One knockout tie: two legs with exchanged hosts or a neutral final."""

    id: UUID
    round_number: int
    tie_index: int
    champions_cycles: tuple[int, ...]
    home: CupSlot
    away: CupSlot
    neutral: bool

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            raise ValueError("Champions tie id must be UUID")
        if type(self.round_number) is not int or self.round_number < 1:
            raise ValueError("Champions round must be a positive int")
        if type(self.tie_index) is not int or self.tie_index < 0:
            raise ValueError("Champions tie index must be a non-negative int")
        if (
            type(self.champions_cycles) is not tuple
            or not 1 <= len(self.champions_cycles) <= 2
            or any(type(day) is not int for day in self.champions_cycles)
            or any(not 1 <= day <= 17 for day in self.champions_cycles)
        ):
            raise ValueError("Champions cycles must be one/two cycles within 1..17")
        if not isinstance(self.home, CupSlot) or not isinstance(self.away, CupSlot):
            raise ValueError("Champions tie slots must be CupSlot rows")
        if self.home == self.away:
            raise ValueError("A tie requires two different participants")
        if type(self.neutral) is not bool:
            raise ValueError("Champions tie neutral flag must be bool")
        if not 1 <= self.round_number <= 9 or (self.neutral and self.round_number < 3):
            raise ValueError("Champions tie round is outside the supported tournament sizes")
        expected = (
            (2 * self.round_number - 1,)
            if self.neutral
            else (2 * self.round_number - 1, 2 * self.round_number)
        )
        if self.champions_cycles != expected:
            raise ValueError("Tie cycles differ from the round schedule")


@dataclass(frozen=True)
class ChampionsLeg:
    """One concrete match inside a champions tie (hosts already exchanged)."""

    tie_id: UUID
    round_number: int
    leg_number: int
    champions_cycle: int
    home: CupSlot
    away: CupSlot
    neutral: bool
    phase_day: int

    def __post_init__(self) -> None:
        if not isinstance(self.tie_id, UUID):
            raise ValueError("Champions leg tie id must be UUID")
        if type(self.round_number) is not int or self.round_number < 1:
            raise ValueError("Champions leg round must be a positive int")
        if type(self.leg_number) is not int or self.leg_number < 1:
            raise ValueError("Invalid leg number")
        if type(self.champions_cycle) is not int or not 1 <= self.champions_cycle <= 17:
            raise ValueError("Champions leg cycle must be within 1..17")
        if not isinstance(self.home, CupSlot) or not isinstance(self.away, CupSlot):
            raise ValueError("Champions leg slots must be CupSlot rows")
        if type(self.neutral) is not bool:
            raise ValueError("Champions leg neutral flag must be bool")
        if type(self.phase_day) is not int or self.phase_day != CHAMPIONS_PHASE_DAY:
            raise ValueError("Champions phase day must be day 2")
        if self.home == self.away:
            raise ValueError("A match requires two different participants")
        if not 1 <= self.round_number <= 9 or (self.neutral and self.round_number < 3):
            raise ValueError("Champions leg round is outside the supported tournament sizes")
        if self.leg_number > (1 if self.neutral else 2):
            raise ValueError("Invalid leg number for this tie format")
        if self.champions_cycle != 2 * self.round_number - 2 + self.leg_number:
            raise ValueError("Leg cycle differs from its round and leg number")


@dataclass(frozen=True)
class ChampionsBracket:
    """Immutable knockout dependency graph for one champions group."""

    policy_version: str
    tournament_id: UUID
    group_id: UUID
    tier: int
    seed: int
    participants: tuple[UUID, ...]
    ties: tuple[ChampionsTie, ...]
    rounds: int
    match_days: int
    champions_cycles: tuple[int, ...]
    free_cycles: tuple[int, ...]
    phase_day: int

    def __post_init__(self) -> None:
        if self.policy_version != CHAMPIONS_BRACKET_POLICY:
            raise ValueError("Invalid champions-bracket policy")
        _check_tournament_id(self.tournament_id)
        _check_group_id(self.group_id)
        if type(self.tier) is not int or not 1 <= self.tier <= 5:
            raise ValueError("Champions bracket tier must be 1..5")
        _check_seed(self.seed)
        if (
            type(self.participants) is not tuple
            or not self.participants
            or any(not isinstance(club_id, UUID) for club_id in self.participants)
        ):
            raise ValueError("Champions participants must be a non-empty tuple of UUIDs")
        if len(set(self.participants)) != len(self.participants):
            raise ValueError("Champions participants must be unique")
        if len(self.participants) not in ALLOWED_PARTICIPANT_COUNTS:
            raise ValueError("Participant count must be 8/16/32/64/128/256/512")
        if type(self.ties) is not tuple or not self.ties:
            raise ValueError("Champions ties must be a non-empty tuple")
        for tie in self.ties:
            if not isinstance(tie, ChampionsTie):
                raise ValueError("Champions ties must be ChampionsTie rows")
        tie_ids = [tie.id for tie in self.ties]
        if len(set(tie_ids)) != len(tie_ids):
            raise ValueError("Champions tie IDs must be unique")
        known = set(tie_ids)
        for tie in self.ties:
            for slot in (tie.home, tie.away):
                if slot.kind == "winner" and (slot.tie_id is None or slot.tie_id not in known):
                    raise ValueError("Winner slot references an unknown tie")
        participant_count = len(self.participants)
        expected_rounds = participant_count.bit_length() - 1
        if type(self.rounds) is not int or self.rounds != expected_rounds:
            raise ValueError("Rounds must equal log2(N)")
        expected_days = 2 * expected_rounds - 1
        if type(self.match_days) is not int or self.match_days != expected_days:
            raise ValueError("Match days must equal 2*log2(N)-1")
        if (
            type(self.champions_cycles) is not tuple
            or any(type(day) is not int for day in self.champions_cycles)
            or self.champions_cycles != tuple(range(1, expected_days + 1))
        ):
            raise ValueError("Champions cycles must run 1..D")
        if (
            type(self.free_cycles) is not tuple
            or any(type(day) is not int for day in self.free_cycles)
            or self.free_cycles != tuple(range(expected_days + 1, TOTAL_CHAMPIONS_CYCLES + 1))
        ):
            raise ValueError("Free cycles must run D+1..18")
        if not self.free_cycles or self.free_cycles[-1] != TOTAL_CHAMPIONS_CYCLES:
            raise ValueError("Cycle 18 is always free")
        if RESERVE_CHAMPIONS_CYCLE in self.champions_cycles:
            raise ValueError("Cycle 18 is always free")
        if type(self.phase_day) is not int or self.phase_day != CHAMPIONS_PHASE_DAY:
            raise ValueError("Champions phase day must be day 2")
        _require_acyclic(self.ties)
        _validate_layout(self)


def tie_id_for(tournament_id: UUID, group_id: UUID, round_number: int, tie_index: int) -> UUID:
    """Return the stable tie ID in the tournament/group/policy/round/index namespace."""
    _check_tournament_id(tournament_id)
    _check_group_id(group_id)
    if type(round_number) is not int or round_number < 1:
        raise ValueError("Round must be a positive int")
    if type(tie_index) is not int or tie_index < 0:
        raise ValueError("Tie index must be a non-negative int")
    return uuid5(
        CHAMPIONS_BRACKET_NAMESPACE,
        f"{CHAMPIONS_BRACKET_POLICY}:{tournament_id.hex}:{group_id.hex}"
        f":round:{round_number}:index:{tie_index}",
    )


def rounds_for(participant_count: int) -> int:
    """Return ``log2(N)`` for 8/16/32/64/128/256/512 clubs."""
    if type(participant_count) is not int or participant_count not in ALLOWED_PARTICIPANT_COUNTS:
        raise ValueError("Participant count must be 8/16/32/64/128/256/512")
    return participant_count.bit_length() - 1


def match_days_for(participant_count: int) -> int:
    """Return ``2*log2(N)-1`` match days (5/7/9/11/13/15/17)."""
    return 2 * rounds_for(participant_count) - 1


def champions_cycles_for(participant_count: int) -> tuple[int, ...]:
    """Return structural champions cycles ``1..D`` for N clubs."""
    days = match_days_for(participant_count)
    return tuple(range(1, days + 1))


def free_cycles_for(participant_count: int) -> tuple[int, ...]:
    """Return free structural cycles ``D+1..18`` for N clubs."""
    days = match_days_for(participant_count)
    return tuple(range(days + 1, TOTAL_CHAMPIONS_CYCLES + 1))


def _expected_tie_cycles(round_number: int, total_rounds: int) -> tuple[int, ...]:
    if round_number < total_rounds:
        return (2 * round_number - 1, 2 * round_number)
    return (2 * total_rounds - 1,)


def _require_acyclic(ties: tuple[ChampionsTie, ...]) -> None:
    index: dict[UUID, ChampionsTie] = {tie.id: tie for tie in ties}
    visiting: set[UUID] = set()
    visited: set[UUID] = set()

    def visit(tie_id: UUID) -> None:
        if tie_id in visited:
            return
        if tie_id in visiting:
            raise ValueError("Champions dependency cycle detected")
        visiting.add(tie_id)
        tie = index[tie_id]
        for slot in (tie.home, tie.away):
            if slot.kind == "winner" and slot.tie_id is not None:
                visit(slot.tie_id)
        visiting.remove(tie_id)
        visited.add(tie_id)

    for tie in ties:
        visit(tie.id)


def _validate_layout(bracket: ChampionsBracket) -> None:
    """Validate a restored graph as strictly as a freshly drawn bracket."""
    participant_count = len(bracket.participants)
    total_rounds = bracket.rounds
    participants = frozenset(bracket.participants)
    index = {tie.id: tie for tie in bracket.ties}

    expected_counts: dict[int, int] = {
        round_number: participant_count // (2**round_number)
        for round_number in range(1, total_rounds + 1)
    }
    actual_counts = Counter(tie.round_number for tie in bracket.ties)
    if dict(actual_counts) != expected_counts:
        raise ValueError("Champions bracket must contain every round with N/2**r ties")
    if len(bracket.ties) != participant_count - 1:
        raise ValueError("Champions bracket must hold N-1 ties")
    if len(all_matches(bracket)) != 2 * participant_count - 3:
        raise ValueError("Champions bracket must hold 2N-3 matches")

    finals = [tie for tie in bracket.ties if tie.round_number == total_rounds]
    if len(finals) != 1:
        raise ValueError("Champions bracket must hold exactly one final")
    final = finals[0]
    if not final.neutral:
        raise ValueError("Final must be neutral")
    if final.champions_cycles != (2 * total_rounds - 1,):
        raise ValueError("Final must be a single match in cycle 2R-1")

    direct: list[UUID] = []
    for tie in bracket.ties:
        expected_cycles = _expected_tie_cycles(tie.round_number, total_rounds)
        if tie.champions_cycles != expected_cycles:
            raise ValueError("Tie cycles differ from the round schedule")
        if tie.neutral != (tie.round_number == total_rounds):
            raise ValueError("Only the final is neutral")
        expected_id = tie_id_for(
            bracket.tournament_id, bracket.group_id, tie.round_number, tie.tie_index
        )
        if tie.id != expected_id:
            raise ValueError("Tie ID differs from the tournament namespace")
        if not 0 <= tie.tie_index < expected_counts[tie.round_number]:
            raise ValueError("Tie index out of range for its round")
        if tie.round_number == 1:
            for slot in (tie.home, tie.away):
                if slot.kind != "club" or slot.club_id is None:
                    raise ValueError("First-round ties hold direct clubs only")
                if slot.club_id not in participants:
                    raise ValueError("Tie club is not a bracket participant")
                direct.append(slot.club_id)
        else:
            for slot in (tie.home, tie.away):
                if slot.kind != "winner" or slot.tie_id is None:
                    raise ValueError("Later rounds hold winner slots only")
                feeder = index.get(slot.tie_id)
                if feeder is None:
                    raise ValueError("Winner slot references an unknown tie")
                if feeder.round_number != tie.round_number - 1:
                    raise ValueError("Winner must feed the next round")
    if set(direct) != participants or len(direct) != participant_count:
        raise ValueError("Bracket must contain exactly its frozen participants once each")

    consumers: Counter[UUID] = Counter()
    for tie in bracket.ties:
        for slot in (tie.home, tie.away):
            if slot.kind == "winner" and slot.tie_id is not None:
                consumers[slot.tie_id] += 1
    for tie in bracket.ties:
        if consumers[tie.id] != (0 if tie.round_number == total_rounds else 1):
            raise ValueError("Every non-final tie must feed exactly one next tie")


def _draw_key(tournament_id: UUID, group_id: UUID) -> str:
    return f"{CHAMPIONS_BRACKET_POLICY}:{tournament_id.hex}:{group_id.hex}:round-1:pool"


def build_champions_bracket(
    *,
    group: ChampionsGroup,
    tournament_id: UUID,
    seed: int,
) -> ChampionsBracket:
    """Build the ordinary knockout bracket for one frozen champions group.

    :param group: frozen :class:`ChampionsGroup` with ``N`` unique
        participants (8/16/32/64/128/256/512). Qualification stays pinned
        there; this function never reselects top-eight clubs.
    :param tournament_id: orchestration-chosen tournament UUID namespacing
        stable tie IDs. Never pass an arbitrary season of one participant.
    :param seed: explicit first-round draw seed (nonnegative BIGINT,
        ``bool`` rejected).
    :returns: immutable bracket with ``N-1`` ties and stable IDs.
    :raises ValueError: on bad IDs, a wrong participant count, duplicated
        clubs or an invalid seed. Missing qualification is never filled
        with invented clubs.
    """
    if not isinstance(group, ChampionsGroup):
        raise ValueError("Group must be a ChampionsGroup")
    _check_tournament_id(tournament_id)
    _check_seed(seed)
    clubs = [participant.club_id for participant in group.participants]
    participant_count = len(clubs)
    if participant_count not in ALLOWED_PARTICIPANT_COUNTS:
        raise ValueError("Participant count must be 8/16/32/64/128/256/512")
    if len(set(clubs)) != participant_count:
        raise ValueError("Champions group clubs must be unique")
    if participant_count != group.participant_count:
        raise ValueError("Group participant rows differ from the participant count")

    total_rounds = rounds_for(participant_count)
    total_days = match_days_for(participant_count)

    base = sorted(clubs, key=lambda club_id: club_id.hex)
    ordered = list(base)
    Random(stable_seed(seed, _draw_key(tournament_id, group.id))).shuffle(ordered)

    ties: list[ChampionsTie] = []
    previous_ids: list[UUID] = []
    for round_number in range(1, total_rounds + 1):
        if round_number == 1:
            pool: list[CupSlot] = [club_slot(club_id) for club_id in ordered]
        else:
            pool = [winner_slot(tie_id) for tie_id in previous_ids]
        expected_ties = participant_count // (2**round_number)
        if len(pool) != expected_ties * 2:
            raise ValueError("Bracket pool differs from the round size")
        current_ids: list[UUID] = []
        for tie_index in range(expected_ties):
            tie_id = tie_id_for(tournament_id, group.id, round_number, tie_index)
            tie = ChampionsTie(
                id=tie_id,
                round_number=round_number,
                tie_index=tie_index,
                champions_cycles=_expected_tie_cycles(round_number, total_rounds),
                home=pool[2 * tie_index],
                away=pool[2 * tie_index + 1],
                neutral=round_number == total_rounds,
            )
            ties.append(tie)
            current_ids.append(tie_id)
        previous_ids = current_ids

    participants = tuple(sorted(clubs, key=lambda club_id: club_id.hex))
    return ChampionsBracket(
        policy_version=CHAMPIONS_BRACKET_POLICY,
        tournament_id=tournament_id,
        group_id=group.id,
        tier=group.tier,
        seed=seed,
        participants=participants,
        ties=tuple(ties),
        rounds=total_rounds,
        match_days=total_days,
        champions_cycles=tuple(range(1, total_days + 1)),
        free_cycles=tuple(range(total_days + 1, TOTAL_CHAMPIONS_CYCLES + 1)),
        phase_day=CHAMPIONS_PHASE_DAY,
    )


def validate_against_group(bracket: ChampionsBracket, group: ChampionsGroup) -> None:
    """Check a bracket against its frozen qualifying group.

    The bracket pins the exact participant set; this function rejects
    foreign/lost clubs, a wrong group/tier and a diverging structural
    schedule. It never reselects qualifiers or invents past tables.
    """
    if not isinstance(bracket, ChampionsBracket):
        raise ValueError("Bracket must be a ChampionsBracket")
    if not isinstance(group, ChampionsGroup):
        raise ValueError("Group must be a ChampionsGroup")
    if bracket.group_id != group.id:
        raise ValueError("Bracket group differs from the frozen group")
    if bracket.tier != group.tier:
        raise ValueError("Bracket tier differs from the frozen group")
    group_clubs = {participant.club_id for participant in group.participants}
    if set(bracket.participants) != group_clubs:
        raise ValueError("Bracket participants differ from the frozen group")
    if len(bracket.participants) != group.participant_count:
        raise ValueError("Bracket size differs from the frozen group")
    if bracket.champions_cycles != group.champions_cycles:
        raise ValueError("Bracket cycles differ from the frozen group")
    if bracket.free_cycles != group.free_cycles:
        raise ValueError("Bracket free cycles differ from the frozen group")
    if bracket.phase_day != group.phase_day:
        raise ValueError("Bracket phase differs from the frozen group")


def _tie_index(bracket: ChampionsBracket) -> dict[UUID, ChampionsTie]:
    return {tie.id: tie for tie in bracket.ties}


def _participant_set(bracket: ChampionsBracket) -> frozenset[UUID]:
    return frozenset(bracket.participants)


def tie_by_id(bracket: ChampionsBracket, tie_id: UUID) -> ChampionsTie:
    """Return one tie by stable ID (pure lookup)."""
    if not isinstance(bracket, ChampionsBracket):
        raise ValueError("Bracket must be a ChampionsBracket")
    if not isinstance(tie_id, UUID):
        raise ValueError("Tie id must be UUID")
    for tie in bracket.ties:
        if tie.id == tie_id:
            return tie
    raise KeyError("Unknown champions tie")


def final_tie(bracket: ChampionsBracket) -> ChampionsTie:
    """Return the single neutral final tie."""
    if not isinstance(bracket, ChampionsBracket):
        raise ValueError("Bracket must be a ChampionsBracket")
    for tie in bracket.ties:
        if tie.round_number == bracket.rounds:
            return tie
    raise ValueError("Bracket has no final")  # pragma: no cover - built brackets always have one


def legs_of(tie: ChampionsTie) -> tuple[ChampionsLeg, ...]:
    """Expand a tie into concrete legs with exchanged hosts.

    Two-leg ties swap home/away between legs; the final stays a single
    neutral match. No kickoff instants or jobs are assigned here.
    """
    if not isinstance(tie, ChampionsTie):
        raise ValueError("Tie must be a ChampionsTie")
    if len(tie.champions_cycles) not in (1, 2):
        raise ValueError("Tie cycles must hold one/two champions cycles")
    if tie.neutral:
        if len(tie.champions_cycles) != 1:
            raise ValueError("Neutral final holds exactly one match")
        return (
            ChampionsLeg(
                tie_id=tie.id,
                round_number=tie.round_number,
                leg_number=1,
                champions_cycle=tie.champions_cycles[0],
                home=tie.home,
                away=tie.away,
                neutral=True,
                phase_day=CHAMPIONS_PHASE_DAY,
            ),
        )
    if len(tie.champions_cycles) != 2:
        raise ValueError("Non-final ties need exactly two legs")
    first, second = tie.champions_cycles
    return (
        ChampionsLeg(
            tie_id=tie.id,
            round_number=tie.round_number,
            leg_number=1,
            champions_cycle=first,
            home=tie.home,
            away=tie.away,
            neutral=False,
            phase_day=CHAMPIONS_PHASE_DAY,
        ),
        ChampionsLeg(
            tie_id=tie.id,
            round_number=tie.round_number,
            leg_number=2,
            champions_cycle=second,
            home=tie.away,
            away=tie.home,
            neutral=False,
            phase_day=CHAMPIONS_PHASE_DAY,
        ),
    )


def all_matches(bracket: ChampionsBracket) -> tuple[ChampionsLeg, ...]:
    """Return every concrete match (``2N-3``) ordered by cycle."""
    if not isinstance(bracket, ChampionsBracket):
        raise ValueError("Bracket must be a ChampionsBracket")
    legs: list[ChampionsLeg] = []
    for tie in bracket.ties:
        legs.extend(legs_of(tie))
    ordered = tuple(
        sorted(legs, key=lambda leg: (leg.champions_cycle, leg.tie_id.hex, leg.leg_number))
    )
    for leg in ordered:
        if leg.champions_cycle in bracket.free_cycles:
            raise ValueError("Match in a free champions cycle")
        if leg.champions_cycle == RESERVE_CHAMPIONS_CYCLE:
            raise ValueError("Cycle 18 is always free")
    return ordered


def _check_winners_shape(bracket: ChampionsBracket, winners: Mapping[UUID, UUID]) -> None:
    if not isinstance(winners, Mapping):
        raise ValueError("Winners must be a mapping of tie ID to club ID")
    known = {tie.id for tie in bracket.ties}
    clubs = _participant_set(bracket)
    for tie_id, club_id in winners.items():
        if not isinstance(tie_id, UUID):
            raise ValueError("Winner tie IDs must be UUID")
        if tie_id not in known:
            raise ValueError("Winner references an unknown tie")
        if not isinstance(club_id, UUID):
            raise ValueError("Tie winners must be club UUIDs")
        if club_id not in clubs:
            raise ValueError("Winner is not a bracket participant")


def _possible_slot_inner(
    index: dict[UUID, ChampionsTie],
    clubs: frozenset[UUID],
    slot: CupSlot,
    winners: Mapping[UUID, UUID],
    trail: frozenset[UUID],
) -> frozenset[UUID]:
    if not isinstance(slot, CupSlot):
        raise ValueError("Slot must be a CupSlot")
    if slot.kind == "club":
        if slot.club_id is None or slot.club_id not in clubs:
            raise ValueError("Slot club is not a bracket participant")
        return frozenset({slot.club_id})
    if slot.kind != "winner":
        raise ValueError("Invalid slot kind")
    if slot.tie_id is None or slot.tie_id not in index:
        raise ValueError("Winner slot references an unknown tie")
    if slot.tie_id in winners:
        winner = winners[slot.tie_id]
        if not isinstance(winner, UUID) or winner not in clubs:
            raise ValueError("Winner is not a bracket participant")
        return frozenset({winner})
    if slot.tie_id in trail:
        raise ValueError("Champions dependency cycle detected")
    feeder = index[slot.tie_id]
    next_trail = trail | {slot.tie_id}
    return _possible_slot_inner(
        index, clubs, feeder.home, winners, next_trail
    ) | _possible_slot_inner(index, clubs, feeder.away, winners, next_trail)


def resolve_slot(
    bracket: ChampionsBracket, slot: CupSlot, winners: Mapping[UUID, UUID]
) -> UUID | None:
    """Resolve one slot to a concrete club when already decided.

    Direct slots always resolve; winner slots resolve only when their feeder
    tie has an entry in ``winners``. Unknown ties, foreign clubs and
    malformed mappings raise ``ValueError``. Undecided winners return ``None``.
    """
    if not isinstance(bracket, ChampionsBracket):
        raise ValueError("Bracket must be a ChampionsBracket")
    if not isinstance(slot, CupSlot):
        raise ValueError("Slot must be a CupSlot")
    validate_winners(bracket, winners)
    clubs = _participant_set(bracket)
    index = _tie_index(bracket)
    if slot.kind == "club":
        if slot.club_id is None or slot.club_id not in clubs:
            raise ValueError("Slot club is not a bracket participant")
        return slot.club_id
    if slot.tie_id is None or slot.tie_id not in index:
        raise ValueError("Winner slot references an unknown tie")
    if slot.tie_id not in winners:
        return None
    winner = winners[slot.tie_id]
    if not isinstance(winner, UUID) or winner not in clubs:
        raise ValueError("Winner is not a bracket participant")
    return winner


def possible_clubs(
    bracket: ChampionsBracket, slot: CupSlot, winners: Mapping[UUID, UUID] | None = None
) -> frozenset[UUID]:
    """Return conditional club IDs for one slot.

    Before any result this is the full feeder subtree; after eliminations the
    loser branch disappears while the surviving branch is preserved.
    """
    if not isinstance(bracket, ChampionsBracket):
        raise ValueError("Bracket must be a ChampionsBracket")
    if winners is None:
        winners = {}
    validate_winners(bracket, winners)
    clubs = _participant_set(bracket)
    index = _tie_index(bracket)
    return _possible_slot_inner(index, clubs, slot, winners, frozenset())


def possible_tie_clubs(
    bracket: ChampionsBracket, tie_id: UUID, winners: Mapping[UUID, UUID] | None = None
) -> frozenset[UUID]:
    """Return conditional club IDs that can still appear in a tie."""
    tie = tie_by_id(bracket, tie_id)
    if winners is None:
        winners = {}
    validate_winners(bracket, winners)
    clubs = _participant_set(bracket)
    index = _tie_index(bracket)
    next_trail = frozenset({tie.id})
    return _possible_slot_inner(index, clubs, tie.home, winners, next_trail) | (
        _possible_slot_inner(index, clubs, tie.away, winners, next_trail)
    )


def validate_winners(bracket: ChampionsBracket, winners: Mapping[UUID, UUID]) -> None:
    """Validate decided winners against the dependency graph.

    Rejects unknown ties, foreign clubs, winners outside the tie subtree,
    downstream winners incompatible with decided feeders, the same club
    winning unrelated branches or an unresolved feeder. Draws of single
    matches are never valid: every winner must be one of the two resolved
    participants. Graph cycles and reused feeders have already been rejected
    at :class:`ChampionsBracket` construction.
    """
    if not isinstance(bracket, ChampionsBracket):
        raise ValueError("Bracket must be a ChampionsBracket")
    if not isinstance(winners, Mapping):
        raise ValueError("Winners must be a mapping of tie ID to club ID")
    _check_winners_shape(bracket, winners)
    # A recorded final result can only use two already known participants.
    # Independent completed branches may be omitted; ancestors of a decided tie
    # must be present, so a champion cannot be invented from an unresolved graph.
    for tie in bracket.ties:
        if tie.id not in winners:
            continue
        participants: set[UUID] = set()
        for slot in (tie.home, tie.away):
            if slot.kind == "club":
                if slot.club_id is None:
                    raise ValueError("Club slot carries no club")
                participants.add(slot.club_id)
            else:
                if slot.tie_id is None:
                    raise ValueError("Winner slot references an unknown tie")
                if slot.tie_id not in winners:
                    raise ValueError("Tie winner requires decided feeder ties")
                participants.add(winners[slot.tie_id])
        if winners[tie.id] not in participants:
            raise ValueError("Winner is incompatible with decided participants")


def champion(bracket: ChampionsBracket, winners: Mapping[UUID, UUID]) -> UUID | None:
    """Return the decided champion (final winner) or ``None`` when undecided."""
    validate_winners(bracket, winners)
    return winners.get(final_tie(bracket).id)
