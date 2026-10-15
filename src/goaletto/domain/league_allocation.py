"""Clean cross-set promotion/relegation allocation (AND-119).

Contract
--------
Input: immutable :class:`LeagueSnapshot` rows for one compatible completed
cohort plus an explicit ``seed``.  Each snapshot carries ``cohort_key``,
``season_id``, ``league_id``, ``league_set_id``, ``tier`` (1..5) and the frozen
final order of exactly ten unique club UUIDs (places 1..10).

``allocate`` validates the whole cohort and returns an immutable
:class:`AllocationResult` with one :class:`ClubAssignment` per club
(``club -> destination league``) plus a verifiable per-tier
:class:`TierDraw`.  The result is a snapshot suitable for a future persisted
receipt; this module performs no I/O, no wall-clock reads and no unseeded RNG.

Rules (user decisions override older GDD examples):

* Two clubs up / two down between adjacent tiers, computed simultaneously
  from frozen tables.  Tier 1 never promotes, tier 5 never relegates.
* Places 1-2 promote (tiers 2..5), places 9-10 relegate (tiers 1..4).
  All other places stay in their current league.
* Stayers keep their exact ``league_id``.  Movers shift exactly one tier;
  no club moves twice in one allocation.
* Destination leagues may belong to any set in the cohort.  There is no
  fixed ``5-1 -> 4-1`` ladder.  Clubs from one source league may land in
  different destination leagues.
* For each destination tier, the promoted pool and the relegated pool are
  shuffled separately from the shuffled vacancy (destination) order, using
  independent seeded draws.  Each destination league receives exactly two
  incoming clubs from each applicable neighbour tier, for exactly ten clubs
  per league.
* Determinism: the result depends only on the snapshot contents,
  ``cohort_key`` and ``seed``.  Input order and job timing do not affect it.
  All shuffles run on deterministically sorted bases via ``stable_seed``.

Validation (pure API, orchestration owns deeper compatibility):

* Rejects empty input, duplicated ``league_id``, duplicated clubs (within a
  league or across the cohort), wrong order length, non-UUID ids,
  mixed ``cohort_key`` values, missing tiers 1..5, unbalanced per-tier
  counts and incomplete per-set verticals (each ``league_set_id`` must own
  exactly tiers 1..5).
* Rejects invalid ``seed`` (must fit nonnegative BIGINT, ``bool`` rejected).

Lifecycle note: the result only carries destination tier/league per club.
Age, staff assignment, last-season flags and sponsorship terms are untouched;
Codex wires them exactly once during durable rollover.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from random import Random
from typing import Literal
from uuid import UUID

from goaletto.domain.world import stable_seed

ALLOCATION_POLICY = "league-allocation-v1"

Movement = Literal["promoted", "stayed", "relegated"]

_PROMOTED: Movement = "promoted"
_STAYED: Movement = "stayed"
_RELEGATED: Movement = "relegated"

__all__ = [
    "ALLOCATION_POLICY",
    "ClubAssignment",
    "LeagueSnapshot",
    "TierDraw",
    "AllocationResult",
    "allocate",
]


@dataclass(frozen=True)
class LeagueSnapshot:
    """One frozen final table for a league in a compatible cohort."""

    cohort_key: str
    season_id: UUID
    league_id: UUID
    league_set_id: UUID
    tier: int
    order: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if (
            type(self.cohort_key) is not str
            or not self.cohort_key.strip()
            or len(self.cohort_key) > 128
        ):
            raise ValueError("Cohort key must be a non-empty string")
        for name in ("season_id", "league_id", "league_set_id"):
            if not isinstance(getattr(self, name), UUID):
                raise ValueError(f"League snapshot {name} must be UUID")
        if type(self.tier) is not int or not 1 <= self.tier <= 5:
            raise ValueError("League snapshot tier must be 1..5")
        if type(self.order) is not tuple or len(self.order) != 10:
            raise ValueError("League snapshot order must be ten club IDs")
        for club_id in self.order:
            if not isinstance(club_id, UUID):
                raise ValueError("League snapshot club IDs must be UUID")
        if len(set(self.order)) != 10:
            raise ValueError("League snapshot clubs must be unique")


@dataclass(frozen=True)
class ClubAssignment:
    """Single-club movement: source place plus destination league/tier."""

    club_id: UUID
    source_league_id: UUID
    source_tier: int
    source_place: int
    destination_league_id: UUID
    destination_tier: int
    movement: Movement

    def __post_init__(self) -> None:
        for name in ("club_id", "source_league_id", "destination_league_id"):
            if not isinstance(getattr(self, name), UUID):
                raise ValueError("Club assignment IDs must be UUID")
        if type(self.source_tier) is not int or not 1 <= self.source_tier <= 5:
            raise ValueError("Invalid source tier")
        if type(self.destination_tier) is not int or not 1 <= self.destination_tier <= 5:
            raise ValueError("Invalid destination tier")
        if type(self.source_place) is not int or not 1 <= self.source_place <= 10:
            raise ValueError("Invalid source place")
        if self.movement not in (_PROMOTED, _STAYED, _RELEGATED):
            raise ValueError("Invalid movement")
        if self.movement == _STAYED and (
            self.source_league_id != self.destination_league_id
            or self.source_tier != self.destination_tier
        ):
            raise ValueError("Stayed clubs must keep their league")
        if self.movement == _PROMOTED and self.destination_tier != self.source_tier - 1:
            raise ValueError("Promoted clubs move exactly one tier up")
        if self.movement == _RELEGATED and self.destination_tier != self.source_tier + 1:
            raise ValueError("Relegated clubs move exactly one tier down")


@dataclass(frozen=True)
class TierDraw:
    """Verifiable shuffled order for one destination tier."""

    tier: int
    dest_league_ids: tuple[UUID, ...]
    from_above: tuple[UUID, ...]
    from_below: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if type(self.tier) is not int or not 1 <= self.tier <= 5:
            raise ValueError("Invalid draw tier")
        for name in ("dest_league_ids", "from_above", "from_below"):
            value = getattr(self, name)
            if type(value) is not tuple or any(not isinstance(v, UUID) for v in value):
                raise ValueError("Draw orders must be tuples of UUIDs")
        if self.tier == 1 and self.from_above:
            raise ValueError("Tier 1 has no pool from above")
        if self.tier == 5 and self.from_below:
            raise ValueError("Tier 5 has no pool from below")


@dataclass(frozen=True)
class AllocationResult:
    """Immutable cohort allocation snapshot for a future persisted receipt."""

    policy_version: str
    cohort_key: str
    seed: int
    assignments: tuple[ClubAssignment, ...]
    draws: tuple[TierDraw, ...]

    def __post_init__(self) -> None:
        if self.policy_version != ALLOCATION_POLICY:
            raise ValueError("Invalid allocation policy")
        if type(self.cohort_key) is not str or not self.cohort_key.strip():
            raise ValueError("Invalid cohort key")
        if type(self.seed) is not int or not 0 <= self.seed < 2**63:
            raise ValueError("Seed must fit nonnegative BIGINT")
        if type(self.assignments) is not tuple or not self.assignments:
            raise ValueError("Assignments must be a non-empty tuple")
        if type(self.draws) is not tuple or len(self.draws) != 5:
            raise ValueError("Draws must cover tiers 1..5")

    def destination_for(self, club_id: UUID) -> UUID:
        """Return the destination league for a club (pure lookup)."""
        for assignment in self.assignments:
            if assignment.club_id == club_id:
                return assignment.destination_league_id
        raise KeyError("Unknown club")

    def by_league(self) -> dict[UUID, tuple[UUID, ...]]:
        """Group destination clubs per league, sorted by club hex for stability."""
        grouped: dict[UUID, list[UUID]] = {}
        for assignment in self.assignments:
            grouped.setdefault(assignment.destination_league_id, []).append(assignment.club_id)
        return {
            league_id: tuple(sorted(clubs, key=lambda c: c.hex))
            for league_id, clubs in grouped.items()
        }


def allocate(standings: Sequence[LeagueSnapshot], *, seed: int) -> AllocationResult:
    """Allocate every club to its next-season league.

    :param standings: one snapshot per league in a compatible cohort.
    :param seed: explicit draw seed (nonnegative BIGINT, ``bool`` rejected).
    :returns: immutable allocation with per-club destinations and per-tier draws.
    :raises ValueError: on incomplete/duplicated snapshots, mixed cohort keys
        or incomplete tier 1..5 verticals.
    """
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("Seed must fit nonnegative BIGINT")
    if isinstance(standings, (str, bytes, bytearray, dict)) or not isinstance(standings, Sequence):
        raise ValueError("Standings must be a sequence of snapshots")
    snapshots = list(standings)
    if not snapshots:
        raise ValueError("Cohort must contain at least one league")
    for snapshot in snapshots:
        if not isinstance(snapshot, LeagueSnapshot):
            raise ValueError("Standings must be LeagueSnapshot rows")

    cohort_keys = {snapshot.cohort_key for snapshot in snapshots}
    if len(cohort_keys) != 1:
        raise ValueError("Mixed cohort keys in one allocation")
    cohort_key = next(iter(cohort_keys))

    league_ids = [snapshot.league_id for snapshot in snapshots]
    if len(set(league_ids)) != len(league_ids):
        raise ValueError("Duplicated league snapshots")

    all_clubs: list[UUID] = [club_id for snapshot in snapshots for club_id in snapshot.order]
    if len(set(all_clubs)) != len(all_clubs):
        raise ValueError("All club IDs must be unique across the cohort")

    by_tier: dict[int, list[LeagueSnapshot]] = {tier: [] for tier in range(1, 6)}
    for snapshot in snapshots:
        by_tier[snapshot.tier].append(snapshot)
    if any(not leagues for leagues in by_tier.values()):
        raise ValueError("Incomplete vertical: tiers 1..5 are all required")
    per_tier_counts = {len(leagues) for leagues in by_tier.values()}
    if len(per_tier_counts) != 1:
        raise ValueError("Unbalanced vertical: every tier needs the same league count")

    by_set: dict[UUID, list[LeagueSnapshot]] = {}
    for snapshot in snapshots:
        by_set.setdefault(snapshot.league_set_id, []).append(snapshot)
    for league_set_id, leagues in by_set.items():
        tiers = sorted(snapshot.tier for snapshot in leagues)
        if tiers != [1, 2, 3, 4, 5]:
            raise ValueError(f"Incomplete league set {league_set_id}: tiers 1..5 are required")

    # Deterministic base order independent of input permutation.
    ordered = sorted(snapshots, key=lambda s: (s.league_set_id.hex, s.tier, s.league_id.hex))

    stayers: dict[UUID, ClubAssignment] = {}
    promoted_by_source_tier: dict[int, list[UUID]] = {tier: [] for tier in range(1, 6)}
    relegated_by_source_tier: dict[int, list[UUID]] = {tier: [] for tier in range(1, 6)}
    source_of: dict[UUID, tuple[UUID, int, int]] = {}
    for snapshot in ordered:
        for place, club_id in enumerate(snapshot.order, start=1):
            source_of[club_id] = (snapshot.league_id, snapshot.tier, place)
            if snapshot.tier > 1 and place <= 2:
                promoted_by_source_tier[snapshot.tier].append(club_id)
            elif snapshot.tier < 5 and place >= 9:
                relegated_by_source_tier[snapshot.tier].append(club_id)
            else:
                stayers[club_id] = ClubAssignment(
                    club_id=club_id,
                    source_league_id=snapshot.league_id,
                    source_tier=snapshot.tier,
                    source_place=place,
                    destination_league_id=snapshot.league_id,
                    destination_tier=snapshot.tier,
                    movement=_STAYED,
                )

    draws: list[TierDraw] = []
    mover_destination: dict[UUID, UUID] = {}
    dest_tier_of_league: dict[UUID, int] = {}
    for tier in range(1, 6):
        dest_base = sorted(by_tier[tier], key=lambda s: s.league_id.hex)
        dest_order = list(dest_base)
        Random(stable_seed(seed, f"{ALLOCATION_POLICY}:{cohort_key}:tier:{tier}:dest")).shuffle(
            dest_order
        )
        pool_above_base = (
            [] if tier == 1 else sorted(relegated_by_source_tier[tier - 1], key=lambda c: c.hex)
        )
        pool_below_base = (
            [] if tier == 5 else sorted(promoted_by_source_tier[tier + 1], key=lambda c: c.hex)
        )
        pool_above = list(pool_above_base)
        pool_below = list(pool_below_base)
        if pool_above:
            Random(
                stable_seed(seed, f"{ALLOCATION_POLICY}:{cohort_key}:tier:{tier}:from-above")
            ).shuffle(pool_above)
        if pool_below:
            Random(
                stable_seed(seed, f"{ALLOCATION_POLICY}:{cohort_key}:tier:{tier}:from-below")
            ).shuffle(pool_below)

        leagues_count = len(dest_order)
        if tier > 1 and len(pool_above) != 2 * leagues_count:
            raise ValueError("Relegated pool does not fill tier vacancies")
        if tier < 5 and len(pool_below) != 2 * leagues_count:
            raise ValueError("Promoted pool does not fill tier vacancies")

        for index, dest in enumerate(dest_order):
            dest_tier_of_league[dest.league_id] = tier
            incoming: list[UUID] = []
            if pool_above:
                incoming.extend(pool_above[index * 2 : (index + 1) * 2])
            if pool_below:
                incoming.extend(pool_below[index * 2 : (index + 1) * 2])
            for club_id in incoming:
                if club_id in mover_destination:
                    raise ValueError("A club cannot move twice in one allocation")
                mover_destination[club_id] = dest.league_id
        draws.append(
            TierDraw(
                tier=tier,
                dest_league_ids=tuple(dest.league_id for dest in dest_order),
                from_above=tuple(pool_above),
                from_below=tuple(pool_below),
            )
        )

    assignments: list[ClubAssignment] = list(stayers.values())
    for club_id, dest_league_id in mover_destination.items():
        source_league_id, source_tier, source_place = source_of[club_id]
        dest_tier = dest_tier_of_league[dest_league_id]
        if dest_tier == source_tier - 1:
            movement: Movement = _PROMOTED
        elif dest_tier == source_tier + 1:
            movement = _RELEGATED
        else:  # pragma: no cover - guarded by pool construction
            raise ValueError("Movers must shift exactly one tier")
        assignments.append(
            ClubAssignment(
                club_id=club_id,
                source_league_id=source_league_id,
                source_tier=source_tier,
                source_place=source_place,
                destination_league_id=dest_league_id,
                destination_tier=dest_tier,
                movement=movement,
            )
        )

    assignments.sort(key=lambda a: a.club_id.hex)
    draws.sort(key=lambda d: d.tier)
    result = AllocationResult(
        policy_version=ALLOCATION_POLICY,
        cohort_key=cohort_key,
        seed=seed,
        assignments=tuple(assignments),
        draws=tuple(draws),
    )

    # Defensive post-check: no silent loss or double move.
    if {a.club_id for a in result.assignments} != set(all_clubs):
        raise ValueError("Allocation must preserve every club exactly once")
    return result
