"""Pure 50-club cup bracket with staged entry (AND-123).

Contract
--------
Input: explicit ``season_id`` / ``league_set_id``, an explicit ``seed`` and
immutable membership of five leagues (tiers 1..5, ten unique clubs each) with
a frozen entry order per league.  The order is trusted as given and is never
recomputed from a live table:

* first season: caller passes the saved starting draw (``tie_break_order``);
* later seasons: caller passes the completed final table (places 1..10).

``build_bracket`` returns an immutable :class:`CupBracket`: a dependency graph
of 49 ties / 97 matches with stable IDs, ``club`` or ``winner(tie)`` slots,
cup days / season cycles and home/away legs with field exchange plus one
neutral single-match final.

Schedule (cup day == 1-based season cycle whose day 1 is the cup phase,
``four-day-calendar-v1``; exact kickoff instants and worker jobs are owned by
Codex inside the ten-day horizon and are never assigned here):

* days 1-2: lower 8 of tier 5 (places 3..10) -> 4 winners, 4 ties;
* days 3-4: 4 prelim winners + upper 2 of tier 5 + 10 of tier 4 = 16 -> 8;
* days 5-6: 8 -> 4; days 7-8: 4 -> 2;
* days 9-10: 2 survivors + 30 clubs of tiers 1..3 = 32 -> 16;
* days 11-12: 16 -> 8; days 13-14: 8 -> 4; days 15-16: 4 -> 2;
* day 17: single neutral final; day 18: reserve, no automatic match.

Totals: 49 ties, 97 matches (48 x 2 legs + 1 final), 50 unique entrants each
entering exactly once, one champion, longest path 17 matches (earliest entry:
2+2+2+2+2+2+2+2+1).

Draw: seeded and versioned (``cup-bracket-v1``).  Every pool is sorted by hex
before shuffling with ``stable_seed`` so the result is independent of input
order.  Tie IDs are seed-independent
(``uuid5(season_id, "cup-bracket-v1:<set>:<round>:<index>")``); only the
participant assignment varies with ``seed``.  Rebuilding with the same inputs
replays the same bracket.

Progression (no extra-time / penalty simulation here; draws are never winners;
the shared knockout runtime AND-124 supplies the decided winner later):

* :func:`resolve_slot` returns the concrete club for a slot when known.
* :func:`possible_clubs` / :func:`possible_tie_clubs` return the conditional
  club set for a future slot/tie: after an elimination the loser's branch is
  removed while the other branch is preserved.
* :func:`validate_winners` rejects unknown ties, foreign clubs, winners
  outside the tie's subtree, incompatible downstream winners, duplicate
  winners on unrelated branches and unresolved feeder results. All public
  progression readers validate the whole winners mapping before using it.
  A recorded tie requires both feeder winners (and their ancestors); independent
  unfinished branches may be absent. Full graph validation rejects cycles,
  reused feeder slots, wrong entry rounds and inconsistent round days.
* :func:`champion` returns the final winner when decided.

The bracket never binds future promotion/relegation to an old ladder: it only
consumes the explicitly passed membership for this cup group.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from random import Random
from typing import Literal
from uuid import UUID, uuid5

from goaletto.domain.world import stable_seed

CUP_POLICY = "cup-bracket-v1"
RESERVE_CUP_DAY = 18
FINAL_CUP_DAY = 17
TOTAL_TIES = 49
TOTAL_MATCHES = 97
TOTAL_CLUBS = 50

SlotKind = Literal["club", "winner"]

_ROUND_ORDER: tuple[str, ...] = (
    "prelim",
    "entry-16",
    "entry-8",
    "entry-4",
    "round-32",
    "round-16",
    "quarter",
    "semi",
    "final",
)

_ROUND_LABELS: dict[str, str] = {
    "prelim": "Prelim 8→4",
    "entry-16": "Entry round 16→8",
    "entry-8": "Entry round 8→4",
    "entry-4": "Entry round 4→2",
    "round-32": "Round of 32",
    "round-16": "Round of 16",
    "quarter": "Quarterfinal",
    "semi": "Semifinal",
    "final": "Final",
}

_ROUND_DAYS: dict[str, tuple[int, ...]] = {
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

_ROUND_TIE_COUNTS: dict[str, int] = {
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

__all__ = [
    "CUP_POLICY",
    "RESERVE_CUP_DAY",
    "FINAL_CUP_DAY",
    "TOTAL_TIES",
    "TOTAL_MATCHES",
    "TOTAL_CLUBS",
    "CupEntry",
    "CupSlot",
    "CupTie",
    "CupLeg",
    "CupBracket",
    "build_bracket",
    "tie_by_id",
    "final_tie",
    "legs_of",
    "all_matches",
    "resolve_slot",
    "possible_clubs",
    "possible_tie_clubs",
    "validate_winners",
    "champion",
    "club_slot",
    "winner_slot",
]


@dataclass(frozen=True)
class CupEntry:
    """One league's frozen entry order (place 1 at index 0)."""

    league_id: UUID
    tier: int
    order: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.league_id, UUID):
            raise ValueError("Cup entry league_id must be UUID")
        if type(self.tier) is not int or not 1 <= self.tier <= 5:
            raise ValueError("Cup entry tier must be 1..5")
        if type(self.order) is not tuple or len(self.order) != 10:
            raise ValueError("Cup entry order must be ten club IDs")
        for club_id in self.order:
            if not isinstance(club_id, UUID):
                raise ValueError("Cup entry club IDs must be UUID")
        if len(set(self.order)) != 10:
            raise ValueError("Cup entry clubs must be unique")


@dataclass(frozen=True)
class CupSlot:
    """One side of a tie: a direct club or the winner of a feeder tie."""

    kind: SlotKind
    club_id: UUID | None
    tie_id: UUID | None

    def __post_init__(self) -> None:
        if self.kind not in ("club", "winner"):
            raise ValueError("Invalid cup slot kind")
        if self.kind == "club":
            if not isinstance(self.club_id, UUID) or self.tie_id is not None:
                raise ValueError("Club slots carry exactly one club ID")
        else:
            if not isinstance(self.tie_id, UUID) or self.club_id is not None:
                raise ValueError("Winner slots carry exactly one tie ID")


def club_slot(club_id: UUID) -> CupSlot:
    """Build a direct-club slot (validates UUID)."""
    if not isinstance(club_id, UUID):
        raise ValueError("Club slot needs a UUID")
    return CupSlot(kind="club", club_id=club_id, tie_id=None)


def winner_slot(tie_id: UUID) -> CupSlot:
    """Build a winner-of-tie slot (validates UUID)."""
    if not isinstance(tie_id, UUID):
        raise ValueError("Winner slot needs a tie UUID")
    return CupSlot(kind="winner", club_id=None, tie_id=tie_id)


@dataclass(frozen=True)
class CupTie:
    """One knockout tie: two legs with exchanged hosts or a neutral final."""

    id: UUID
    round_key: str
    round_label: str
    cup_days: tuple[int, ...]
    home: CupSlot
    away: CupSlot
    neutral: bool

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            raise ValueError("Cup tie id must be UUID")
        if self.round_key not in _ROUND_ORDER:
            raise ValueError("Unknown cup round")
        if not isinstance(self.round_label, str) or not self.round_label.strip():
            raise ValueError("Cup tie needs a round label")
        if (
            type(self.cup_days) is not tuple
            or not 1 <= len(self.cup_days) <= 2
            or any(type(day) is not int for day in self.cup_days)
            or any(not 1 <= day <= 17 for day in self.cup_days)
        ):
            raise ValueError("Cup days must be one/two days within 1..17")
        if self.cup_days != _ROUND_DAYS[self.round_key]:
            raise ValueError("Cup days differ from the round schedule")
        if not isinstance(self.home, CupSlot) or not isinstance(self.away, CupSlot):
            raise ValueError("Cup tie slots must be CupSlot rows")
        if self.home == self.away:
            raise ValueError("A tie requires two different participants")
        if type(self.neutral) is not bool:
            raise ValueError("Cup tie neutral flag must be bool")
        if self.round_key == "final":
            if len(self.cup_days) != 1 or self.cup_days[0] != FINAL_CUP_DAY:
                raise ValueError("Final is a single match on cup day 17")
            if not self.neutral:
                raise ValueError("Final must be neutral")
        elif len(self.cup_days) != 2:
            raise ValueError("Non-final ties need exactly two legs")
        elif self.neutral:
            raise ValueError("Only the final is neutral")


@dataclass(frozen=True)
class CupLeg:
    """One concrete match inside a tie (hosts already exchanged)."""

    tie_id: UUID
    round_key: str
    leg_number: int
    cup_day: int
    home: CupSlot
    away: CupSlot
    neutral: bool

    def __post_init__(self) -> None:
        if not isinstance(self.tie_id, UUID):
            raise ValueError("Cup leg tie id must be UUID")
        if self.round_key not in _ROUND_ORDER:
            raise ValueError("Unknown cup round")
        if type(self.leg_number) is not int or self.leg_number < 1:
            raise ValueError("Invalid leg number")
        if type(self.cup_day) is not int or not 1 <= self.cup_day <= 17:
            raise ValueError("Cup leg day must be within 1..17")
        if not isinstance(self.home, CupSlot) or not isinstance(self.away, CupSlot):
            raise ValueError("Cup leg slots must be CupSlot rows")
        if type(self.neutral) is not bool:
            raise ValueError("Cup leg neutral flag must be bool")
        days = _ROUND_DAYS[self.round_key]
        if self.leg_number > len(days) or self.cup_day != days[self.leg_number - 1]:
            raise ValueError("Cup leg differs from the round schedule")
        if self.neutral != (self.round_key == "final"):
            raise ValueError("Only the final is neutral")
        if self.home == self.away:
            raise ValueError("A match requires two different participants")


@dataclass(frozen=True)
class CupBracket:
    """Immutable cup dependency graph for one season group."""

    policy_version: str
    season_id: UUID
    league_set_id: UUID
    seed: int
    entries: tuple[CupEntry, ...]
    ties: tuple[CupTie, ...]

    def __post_init__(self) -> None:
        if self.policy_version != CUP_POLICY:
            raise ValueError("Invalid cup policy")
        if not isinstance(self.season_id, UUID):
            raise ValueError("Cup season_id must be UUID")
        if not isinstance(self.league_set_id, UUID):
            raise ValueError("Cup league_set_id must be UUID")
        if type(self.seed) is not int or not 0 <= self.seed < 2**63:
            raise ValueError("Seed must fit nonnegative BIGINT")
        if type(self.entries) is not tuple or not self.entries:
            raise ValueError("Cup entries must be a non-empty tuple")
        for entry in self.entries:
            if not isinstance(entry, CupEntry):
                raise ValueError("Cup entries must be CupEntry rows")
        if type(self.ties) is not tuple or not self.ties:
            raise ValueError("Cup ties must be a non-empty tuple")
        for tie in self.ties:
            if not isinstance(tie, CupTie):
                raise ValueError("Cup ties must be CupTie rows")
        tie_ids = [tie.id for tie in self.ties]
        if len(set(tie_ids)) != len(tie_ids):
            raise ValueError("Cup tie IDs must be unique")
        known = set(tie_ids)
        for tie in self.ties:
            for slot in (tie.home, tie.away):
                if slot.kind == "winner" and slot.tie_id not in known:
                    raise ValueError("Winner slot references an unknown tie")
        # Each club enters directly exactly once at most.
        direct: list[UUID] = []
        for tie in self.ties:
            for slot in (tie.home, tie.away):
                if slot.kind == "club" and slot.club_id is not None:
                    direct.append(slot.club_id)
        if len(set(direct)) != len(direct):
            raise ValueError("Each club enters the cup exactly once")
        _require_acyclic(self.ties)
        _validate_layout(self)


def _validate_entries(entries: Sequence[CupEntry]) -> dict[int, CupEntry]:
    if len(entries) != 5 or any(not isinstance(entry, CupEntry) for entry in entries):
        raise ValueError("Cup needs exactly five leagues (tiers 1..5)")
    if sorted(entry.tier for entry in entries) != [1, 2, 3, 4, 5]:
        raise ValueError("Cup needs exactly tiers 1..5")
    if len({entry.league_id for entry in entries}) != 5:
        raise ValueError("Duplicated cup league")
    clubs = [club for entry in entries for club in entry.order]
    if len(set(clubs)) != TOTAL_CLUBS:
        raise ValueError("Cup needs 50 unique clubs")
    return {entry.tier: entry for entry in entries}


def _entry_rounds(entries: Mapping[int, CupEntry]) -> dict[UUID, str]:
    return {
        club: ("round-32" if tier <= 3 else "entry-16" if tier == 4 or place <= 2 else "prelim")
        for tier, entry in entries.items()
        for place, club in enumerate(entry.order, 1)
    }


def _validate_layout(bracket: CupBracket) -> None:
    """Validate a restored graph as strictly as a freshly drawn full cup."""
    entries = _validate_entries(bracket.entries)
    if Counter(tie.round_key for tie in bracket.ties) != _ROUND_TIE_COUNTS:
        raise ValueError("Cup must contain every round and exactly 49 ties")
    entrant_round = _entry_rounds(entries)
    direct = {
        slot.club_id for tie in bracket.ties for slot in (tie.home, tie.away) if slot.kind == "club"
    }
    if direct != set(entrant_round):
        raise ValueError("Cup graph must contain exactly its frozen entrants")
    index = {tie.id: tie for tie in bracket.ties}
    previous = dict(zip(_ROUND_ORDER[1:], _ROUND_ORDER[:-1], strict=True))
    consumers: Counter[UUID] = Counter()
    for tie in bracket.ties:
        for slot in (tie.home, tie.away):
            if slot.kind == "club":
                assert slot.club_id is not None
                if entrant_round[slot.club_id] != tie.round_key:
                    raise ValueError("Club enters in the wrong cup round")
            else:
                assert slot.tie_id is not None
                if index[slot.tie_id].round_key != previous.get(tie.round_key):
                    raise ValueError("Winner must feed the next cup round")
                consumers[slot.tie_id] += 1
    for tie in bracket.ties:
        if consumers[tie.id] != (0 if tie.round_key == "final" else 1):
            raise ValueError("Every non-final tie must feed exactly one next tie")


def _require_acyclic(ties: tuple[CupTie, ...]) -> None:
    index: dict[UUID, CupTie] = {tie.id: tie for tie in ties}
    visiting: set[UUID] = set()
    visited: set[UUID] = set()

    def visit(tie_id: UUID) -> None:
        if tie_id in visited:
            return
        if tie_id in visiting:
            raise ValueError("Cup dependency cycle detected")
        visiting.add(tie_id)
        tie = index[tie_id]
        for slot in (tie.home, tie.away):
            if slot.kind == "winner" and slot.tie_id is not None:
                visit(slot.tie_id)
        visiting.remove(tie_id)
        visited.add(tie_id)

    for tie in ties:
        visit(tie.id)


def _check_seed(seed: int) -> None:
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("Seed must fit nonnegative BIGINT")


def _tie_id(season_id: UUID, league_set_id: UUID, round_key: str, index: int) -> UUID:
    return uuid5(season_id, f"{CUP_POLICY}:{league_set_id.hex}:{round_key}:{index}")


def _shuffled(items: list[CupSlot], *, seed: int, key: str) -> list[CupSlot]:
    ordered = list(items)
    Random(stable_seed(seed, key)).shuffle(ordered)
    return ordered


def build_bracket(
    *,
    season_id: UUID,
    league_set_id: UUID,
    entries: Sequence[CupEntry],
    seed: int,
) -> CupBracket:
    """Build the 50-club staged-entry bracket.

    :param season_id: owning season (namespaces stable tie IDs).
    :param league_set_id: owning league set (one cup group = tiers 1..5).
    :param entries: exactly five :class:`CupEntry` rows, one per tier 1..5,
        each with ten unique clubs (place 1 at index 0).  Tier 5 places 1-2
        enter on days 3-4, places 3-10 on days 1-2; tier 4 enters days 3-4;
        tiers 1..3 enter days 9-10.  The order is used as given.
    :param seed: explicit draw seed (nonnegative BIGINT, ``bool`` rejected).
    :returns: immutable bracket with 49 ties and stable IDs.
    :raises ValueError: on bad IDs, duplicated clubs/leagues, wrong tier
        coverage or an invalid seed.
    """
    if not isinstance(season_id, UUID):
        raise ValueError("Cup season_id must be UUID")
    if not isinstance(league_set_id, UUID):
        raise ValueError("Cup league_set_id must be UUID")
    _check_seed(seed)
    if isinstance(entries, (str, bytes, bytearray, dict)) or not isinstance(entries, Sequence):
        raise ValueError("Cup entries must be a sequence of CupEntry rows")
    snapshot = list(entries)
    by_tier = _validate_entries(snapshot)
    entry_round = _entry_rounds(by_tier)

    def draw_key(round_key: str, suffix: str) -> str:
        return f"{CUP_POLICY}:{season_id.hex}:{league_set_id.hex}:{round_key}:{suffix}"

    ties: list[CupTie] = []

    def make_tie(
        round_key: str, index: int, home: CupSlot, away: CupSlot, *, neutral: bool = False
    ) -> CupTie:
        return CupTie(
            id=_tie_id(season_id, league_set_id, round_key, index),
            round_key=round_key,
            round_label=_ROUND_LABELS[round_key],
            cup_days=_ROUND_DAYS[round_key],
            home=home,
            away=away,
            neutral=neutral,
        )

    # Each stage adds only its direct entrants to the preceding winners.
    # Stable sorted bases preserve the v1 draw when input order changes.
    previous_ids: list[UUID] = []
    for round_key in _ROUND_ORDER:
        pool = [winner_slot(tie_id) for tie_id in sorted(previous_ids, key=lambda c: c.hex)]
        pool.extend(
            club_slot(club)
            for club in sorted(
                (club for club, entry in entry_round.items() if entry == round_key),
                key=lambda c: c.hex,
            )
        )
        drawn = _shuffled(pool, seed=seed, key=draw_key(round_key, "pool"))
        previous_ids = []
        for index in range(_ROUND_TIE_COUNTS[round_key]):
            tie = make_tie(
                round_key,
                index,
                drawn[2 * index],
                drawn[2 * index + 1],
                neutral=round_key == "final",
            )
            ties.append(tie)
            previous_ids.append(tie.id)

    ordered_entries = tuple(sorted(snapshot, key=lambda entry: entry.tier))
    bracket = CupBracket(
        policy_version=CUP_POLICY,
        season_id=season_id,
        league_set_id=league_set_id,
        seed=seed,
        entries=ordered_entries,
        ties=tuple(ties),
    )
    if len(bracket.ties) != TOTAL_TIES:
        raise ValueError("Cup must hold 49 ties")
    if len(all_matches(bracket)) != TOTAL_MATCHES:
        raise ValueError("Cup must hold 97 matches")
    direct_clubs = {
        slot.club_id
        for tie in bracket.ties
        for slot in (tie.home, tie.away)
        if slot.kind == "club" and slot.club_id is not None
    }
    if len(direct_clubs) != TOTAL_CLUBS:
        raise ValueError("Cup must involve 50 unique entrants")
    return bracket


def _tie_index(bracket: CupBracket) -> dict[UUID, CupTie]:
    return {tie.id: tie for tie in bracket.ties}


def _direct_clubs(bracket: CupBracket) -> frozenset[UUID]:
    return frozenset(
        slot.club_id
        for tie in bracket.ties
        for slot in (tie.home, tie.away)
        if slot.kind == "club" and slot.club_id is not None
    )


def tie_by_id(bracket: CupBracket, tie_id: UUID) -> CupTie:
    """Return one tie by stable ID (pure lookup)."""
    if not isinstance(bracket, CupBracket):
        raise ValueError("Bracket must be a CupBracket")
    if not isinstance(tie_id, UUID):
        raise ValueError("Tie id must be UUID")
    for tie in bracket.ties:
        if tie.id == tie_id:
            return tie
    raise KeyError("Unknown cup tie")


def final_tie(bracket: CupBracket) -> CupTie:
    """Return the single neutral final tie."""
    for tie in bracket.ties:
        if tie.round_key == "final":
            return tie
    raise ValueError("Bracket has no final")  # pragma: no cover - built brackets always have one


def legs_of(tie: CupTie) -> tuple[CupLeg, ...]:
    """Expand a tie into concrete legs with exchanged hosts.

    Two-leg ties swap home/away between legs; the final stays a single
    neutral match.  No kickoff instants or jobs are assigned here.
    """
    if not isinstance(tie, CupTie):
        raise ValueError("Tie must be a CupTie")
    if tie.round_key == "final":
        return (
            CupLeg(
                tie_id=tie.id,
                round_key=tie.round_key,
                leg_number=1,
                cup_day=tie.cup_days[0],
                home=tie.home,
                away=tie.away,
                neutral=True,
            ),
        )
    first, second = tie.cup_days
    return (
        CupLeg(
            tie_id=tie.id,
            round_key=tie.round_key,
            leg_number=1,
            cup_day=first,
            home=tie.home,
            away=tie.away,
            neutral=False,
        ),
        CupLeg(
            tie_id=tie.id,
            round_key=tie.round_key,
            leg_number=2,
            cup_day=second,
            home=tie.away,
            away=tie.home,
            neutral=False,
        ),
    )


def all_matches(bracket: CupBracket) -> tuple[CupLeg, ...]:
    """Return every concrete match (97 for a full bracket) ordered by day."""
    if not isinstance(bracket, CupBracket):
        raise ValueError("Bracket must be a CupBracket")
    legs: list[CupLeg] = []
    for tie in bracket.ties:
        legs.extend(legs_of(tie))
    return tuple(sorted(legs, key=lambda leg: (leg.cup_day, leg.tie_id.hex, leg.leg_number)))


def _check_winners_shape(bracket: CupBracket, winners: Mapping[UUID, UUID]) -> None:
    if not isinstance(winners, Mapping):
        raise ValueError("Winners must be a mapping of tie ID to club ID")
    known = {tie.id for tie in bracket.ties}
    clubs = _direct_clubs(bracket)
    for tie_id, club_id in winners.items():
        if not isinstance(tie_id, UUID):
            raise ValueError("Winner tie IDs must be UUID")
        if tie_id not in known:
            raise ValueError("Winner references an unknown tie")
        if not isinstance(club_id, UUID):
            raise ValueError("Tie winners must be club UUIDs")
        if club_id not in clubs:
            raise ValueError("Winner is not a cup entrant")


def _possible_slot_inner(
    index: dict[UUID, CupTie],
    clubs: frozenset[UUID],
    slot: CupSlot,
    winners: Mapping[UUID, UUID],
    trail: frozenset[UUID],
) -> frozenset[UUID]:
    if not isinstance(slot, CupSlot):
        raise ValueError("Slot must be a CupSlot")
    if slot.kind == "club":
        if slot.club_id is None or slot.club_id not in clubs:
            raise ValueError("Slot club is not a cup entrant")
        return frozenset({slot.club_id})
    if slot.kind != "winner":
        raise ValueError("Invalid cup slot kind")
    if slot.tie_id is None or slot.tie_id not in index:
        raise ValueError("Winner slot references an unknown tie")
    if slot.tie_id in winners:
        winner = winners[slot.tie_id]
        if not isinstance(winner, UUID) or winner not in clubs:
            raise ValueError("Winner is not a cup entrant")
        return frozenset({winner})
    if slot.tie_id in trail:
        raise ValueError("Cup dependency cycle detected")
    feeder = index[slot.tie_id]
    next_trail = trail | {slot.tie_id}
    return _possible_slot_inner(
        index, clubs, feeder.home, winners, next_trail
    ) | _possible_slot_inner(index, clubs, feeder.away, winners, next_trail)


def resolve_slot(bracket: CupBracket, slot: CupSlot, winners: Mapping[UUID, UUID]) -> UUID | None:
    """Resolve one slot to a concrete club when already decided.

    Direct slots always resolve; winner slots resolve only when their feeder
    tie has an entry in ``winners``.  Unknown ties, foreign clubs and
    malformed mappings raise ``ValueError``.  Undecided winners return ``None``.
    """
    if not isinstance(bracket, CupBracket):
        raise ValueError("Bracket must be a CupBracket")
    if not isinstance(slot, CupSlot):
        raise ValueError("Slot must be a CupSlot")
    validate_winners(bracket, winners)
    clubs = _direct_clubs(bracket)
    index = _tie_index(bracket)
    if slot.kind == "club":
        if slot.club_id is None or slot.club_id not in clubs:
            raise ValueError("Slot club is not a cup entrant")
        return slot.club_id
    if slot.tie_id is None or slot.tie_id not in index:
        raise ValueError("Winner slot references an unknown tie")
    if slot.tie_id not in winners:
        return None
    winner = winners[slot.tie_id]
    if not isinstance(winner, UUID) or winner not in clubs:
        raise ValueError("Winner is not a cup entrant")
    return winner


def possible_clubs(
    bracket: CupBracket, slot: CupSlot, winners: Mapping[UUID, UUID] | None = None
) -> frozenset[UUID]:
    """Return conditional club IDs for one slot.

    Before any result this is the full feeder subtree; after eliminations the
    loser's branch disappears while the surviving branch is preserved.
    """
    if not isinstance(bracket, CupBracket):
        raise ValueError("Bracket must be a CupBracket")
    if winners is None:
        winners = {}
    validate_winners(bracket, winners)
    clubs = _direct_clubs(bracket)
    index = _tie_index(bracket)
    return _possible_slot_inner(index, clubs, slot, winners, frozenset())


def possible_tie_clubs(
    bracket: CupBracket, tie_id: UUID, winners: Mapping[UUID, UUID] | None = None
) -> frozenset[UUID]:
    """Return conditional club IDs that can still appear in a tie."""
    tie = tie_by_id(bracket, tie_id)
    if winners is None:
        winners = {}
    validate_winners(bracket, winners)
    clubs = _direct_clubs(bracket)
    index = _tie_index(bracket)
    next_trail = frozenset({tie.id})
    return _possible_slot_inner(index, clubs, tie.home, winners, next_trail) | (
        _possible_slot_inner(index, clubs, tie.away, winners, next_trail)
    )


def validate_winners(bracket: CupBracket, winners: Mapping[UUID, UUID]) -> None:
    """Validate decided winners against the dependency graph.

    Rejects unknown ties, foreign clubs, winners outside the tie's subtree,
    downstream winners incompatible with decided feeders, the same club
    winning unrelated branches or an unresolved feeder. Draws are never valid:
    every winner must be one of the two resolved participants. Graph cycles and
    reused feeders have already been rejected at CupBracket construction.
    """
    if not isinstance(bracket, CupBracket):
        raise ValueError("Bracket must be a CupBracket")
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
                assert slot.club_id is not None
                participants.add(slot.club_id)
            else:
                assert slot.tie_id is not None
                if slot.tie_id not in winners:
                    raise ValueError("Tie winner requires decided feeder ties")
                participants.add(winners[slot.tie_id])
        if winners[tie.id] not in participants:
            raise ValueError("Winner is incompatible with decided participants")


def champion(bracket: CupBracket, winners: Mapping[UUID, UUID]) -> UUID | None:
    """Return the decided champion (final winner) or ``None`` when undecided."""
    validate_winners(bracket, winners)
    return winners.get(final_tie(bracket).id)
