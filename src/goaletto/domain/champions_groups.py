"""Clean champions-cup qualification and grouping (AND-126).

Contract
--------
Independent computational subpackage, not the whole tournament. Pure Python
with immutable inputs/results and an explicit seed: no DB, network, clocks,
Telegram, worker, aiogram, SQLAlchemy or unseeded RNG.

Input: one compatible qualifying cohort as immutable
:class:`goaletto.domain.league_allocation.LeagueSnapshot` rows plus an
explicit ``seed``. Each snapshot carries ``cohort_key``, ``season_id``,
``league_id``, ``league_set_id``, ``tier`` (1..5) and the frozen final order
of exactly ten unique club UUIDs (places 1..10). Date compatibility and
tournament completion are owned by the orchestration, not by an arbitrary
``cohort_key`` string.

Selection (frozen, no live tables):

* For every tier 1..5 the top eight clubs of each league qualify (places
  1..8). Places 9..10 never qualify. Tiers never mix.
* ``qualifying_tier`` and ``qualifying_season_id`` stay pinned in the result:
  later promotion/relegation does not retroactively change eligibility.
* Current owners and the live table are not inputs of a redraw: a replay must
  reload the same frozen snapshots.

Grouping (powers of two, at most 64 sets per group):

* ``split_group_sizes(N)`` returns the canonical decomposition: greedy
  largest power of two while ``N <= 64`` (binary decomposition, fewest
  groups), and repeated ``64`` plus the same decomposition of the remainder
  while ``N > 64``. Examples: 1 -> (1,), 2 -> (2,), 3 -> (2, 1),
  4 -> (4,), 6 -> (4, 2), 7 -> (4, 2, 1), 65 -> (64, 1),
  129 -> (64, 64, 1).
* Every set enters exactly one group of every tier; every qualified club
  enters exactly one tournament of its qualifying tier.
* Distribution is seeded, reproducible and independent of input order. The
  base set order is sorted by hex and only then shuffled with ``stable_seed``,
  so the lowest UUIDs are not pinned into the large group. Each tier uses its
  own explicit draw key
  ``f"{CHAMPIONS_GROUPS_POLICY}:{cohort_key}:tier:{tier}:sets"``.

Structural schedule (no detailed bracket here):

* Group with ``G`` sets holds ``8 * G`` participants, ``R = log2(8 * G)``
  rounds and ``D = 2 * R - 1`` match days: 1/2/4/8/16/32/64 sets map to
  5/7/9/11/13/15/17 days. The first ``R - 1`` rounds are two-leg ties
  (two days each); the final is a single neutral match (one day).
* ``champions_cycles`` are the structural numbers ``1..D``; ``free_cycles``
  are ``D + 1..18`` (cycle 18 is always free). Every such day is day 2 of
  the four-day cycle (``CHAMPIONS_PHASE_DAY = 2``). No kickoff instants or
  worker jobs are assigned here.
* No second shared knockout engine is built and the 50-club staged-entry cup
  is untouched. Detailed pairings, extra time/penalties (AND-124 runtime),
  venue, kickoff/jobs inside the ten-day horizon, payouts and UI stay with
  the central integration (Codex).

Stable IDs and replay:

* ``group_id_for(cohort_key, tier, group_index)`` and
  ``participant_id_for(cohort_key, tier, club_id)`` use
  ``uuid5(CHAMPIONS_NAMESPACE, f"{policy}:{cohort}:{tier}:...")``.
  Group IDs are seed-independent (index-based); only the member assignment
  varies with ``seed``. Participant IDs are seed-independent (club-based).
  Neither job timing nor seedless ``random`` affects the result.
* To replay, persist: ``policy_version``, ``cohort_key``, ``seed``, the full
  frozen snapshots (set/league/season IDs plus ten-club orders) and the
  per-tier draws (``ordered_set_ids``, ``group_sizes``, ``group_ids``).
  Rebuilding with the same inputs replays the same groups. Never recompute
  from live tables on retry.

Bootstrap:

* The API works only with an explicitly passed full completed cohort. An
  empty cohort, a missing tier/set vertical or a first season without a past
  table is rejected with ``ValueError`` and must be surfaced as "no
  qualification", never filled with invented clubs. The bootstrap policy for
  the first season and mid-season sets is a separate pre-launch decision and
  is not chosen here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from random import Random
from uuid import UUID, uuid5

from goaletto.domain.league_allocation import LeagueSnapshot
from goaletto.domain.world import stable_seed

CHAMPIONS_GROUPS_POLICY = "champions-groups-v1"
CHAMPIONS_NAMESPACE = UUID("7e8f1a2b-3c4d-5e6f-8a9b-0c1d2e3f4a5b")
MAX_SETS_PER_GROUP = 64
QUALIFIERS_PER_LEAGUE = 8
TOTAL_CHAMPIONS_CYCLES = 18
CHAMPIONS_PHASE_DAY = 2

ALLOWED_GROUP_SIZES: tuple[int, ...] = (1, 2, 4, 8, 16, 32, 64)
ALLOWED_PARTICIPANT_COUNTS: tuple[int, ...] = (8, 16, 32, 64, 128, 256, 512)

__all__ = [
    "CHAMPIONS_GROUPS_POLICY",
    "CHAMPIONS_NAMESPACE",
    "MAX_SETS_PER_GROUP",
    "QUALIFIERS_PER_LEAGUE",
    "TOTAL_CHAMPIONS_CYCLES",
    "CHAMPIONS_PHASE_DAY",
    "ALLOWED_GROUP_SIZES",
    "ALLOWED_PARTICIPANT_COUNTS",
    "ChampionsParticipant",
    "ChampionsGroup",
    "ChampionsTierDraw",
    "ChampionsGroupsResult",
    "split_group_sizes",
    "rounds_for",
    "match_days_for",
    "champions_cycles_for",
    "free_cycles_for",
    "group_id_for",
    "participant_id_for",
    "build_champions_groups",
]


def _check_seed(seed: int) -> None:
    if type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("Seed must fit nonnegative BIGINT")


def _check_cohort_key(cohort_key: str) -> None:
    if type(cohort_key) is not str or not cohort_key.strip() or len(cohort_key) > 128:
        raise ValueError("Cohort key must be a non-empty string")


def _check_tier(tier: int) -> None:
    if type(tier) is not int or not 1 <= tier <= 5:
        raise ValueError("Tier must be 1..5")


def _largest_power_of_two_at_most(value: int) -> int:
    return 1 << (value.bit_length() - 1)


@dataclass(frozen=True)
class ChampionsParticipant:
    """One qualified club pinned to its qualifying league/season/tier."""

    participant_id: UUID
    club_id: UUID
    source_league_id: UUID
    source_league_set_id: UUID
    qualifying_season_id: UUID
    qualifying_tier: int
    qualifying_place: int

    def __post_init__(self) -> None:
        for name in (
            "participant_id",
            "club_id",
            "source_league_id",
            "source_league_set_id",
            "qualifying_season_id",
        ):
            if not isinstance(getattr(self, name), UUID):
                raise ValueError(f"Champions participant {name} must be UUID")
        if type(self.qualifying_tier) is not int or not 1 <= self.qualifying_tier <= 5:
            raise ValueError("Qualifying tier must be 1..5")
        if type(self.qualifying_place) is not int or not 1 <= self.qualifying_place <= 8:
            raise ValueError("Qualifying place must be 1..8")


@dataclass(frozen=True)
class ChampionsGroup:
    """One per-tier tournament: G sets, 8*G clubs and a structural schedule."""

    id: UUID
    tier: int
    group_index: int
    league_set_ids: tuple[UUID, ...]
    participants: tuple[ChampionsParticipant, ...]
    participant_count: int
    rounds: int
    match_days: int
    champions_cycles: tuple[int, ...]
    free_cycles: tuple[int, ...]
    phase_day: int

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            raise ValueError("Champions group id must be UUID")
        if type(self.tier) is not int or not 1 <= self.tier <= 5:
            raise ValueError("Champions group tier must be 1..5")
        if type(self.group_index) is not int or self.group_index < 0:
            raise ValueError("Champions group index must be a non-negative int")
        if (
            type(self.league_set_ids) is not tuple
            or not self.league_set_ids
            or any(not isinstance(v, UUID) for v in self.league_set_ids)
        ):
            raise ValueError("Champions group sets must be a non-empty tuple of UUIDs")
        if len(set(self.league_set_ids)) != len(self.league_set_ids):
            raise ValueError("Champions group sets must be unique")
        if len(self.league_set_ids) not in ALLOWED_GROUP_SIZES:
            raise ValueError("Champions group size must be a power of two 1..64")
        if type(self.participants) is not tuple or not self.participants:
            raise ValueError("Champions group participants must be a non-empty tuple")
        for participant in self.participants:
            if not isinstance(participant, ChampionsParticipant):
                raise ValueError("Champions group participants must be ChampionsParticipant rows")
            if participant.qualifying_tier != self.tier:
                raise ValueError("Tiers must not mix inside one champions group")
        club_ids = [participant.club_id for participant in self.participants]
        if len(set(club_ids)) != len(club_ids):
            raise ValueError("Champions group clubs must be unique")
        participant_ids = [participant.participant_id for participant in self.participants]
        if len(set(participant_ids)) != len(participant_ids):
            raise ValueError("Champions participant IDs must be unique")
        expected_sets = set(self.league_set_ids)
        actual_sets = {participant.source_league_set_id for participant in self.participants}
        if actual_sets != expected_sets:
            raise ValueError("Champions group sets differ from participant sources")
        for set_id in self.league_set_ids:
            count = sum(
                1 for participant in self.participants if participant.source_league_set_id == set_id
            )
            if count != QUALIFIERS_PER_LEAGUE:
                raise ValueError("Each league contributes exactly eight qualifiers")
        expected_count = QUALIFIERS_PER_LEAGUE * len(self.league_set_ids)
        if type(self.participant_count) is not int or self.participant_count != expected_count:
            raise ValueError("Participant count must equal 8G")
        if self.participant_count != len(self.participants):
            raise ValueError("Participant count differs from participant rows")
        expected_rounds = self.participant_count.bit_length() - 1
        if type(self.rounds) is not int or self.rounds != expected_rounds:
            raise ValueError("Rounds must equal log2(8G)")
        expected_days = 2 * expected_rounds - 1
        if type(self.match_days) is not int or self.match_days != expected_days:
            raise ValueError("Match days must equal 2*log2(8G)-1")
        if self.champions_cycles != tuple(range(1, expected_days + 1)):
            raise ValueError("Champions cycles must run 1..D")
        if self.free_cycles != tuple(range(expected_days + 1, TOTAL_CHAMPIONS_CYCLES + 1)):
            raise ValueError("Free cycles must run D+1..18")
        if not self.free_cycles or self.free_cycles[-1] != TOTAL_CHAMPIONS_CYCLES:
            raise ValueError("Cycle 18 is always free")
        if type(self.phase_day) is not int or self.phase_day != CHAMPIONS_PHASE_DAY:
            raise ValueError("Champions phase day must be day 2")


@dataclass(frozen=True)
class ChampionsTierDraw:
    """Verifiable seeded set order for one tier."""

    tier: int
    ordered_set_ids: tuple[UUID, ...]
    group_sizes: tuple[int, ...]
    group_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        if type(self.tier) is not int or not 1 <= self.tier <= 5:
            raise ValueError("Champions draw tier must be 1..5")
        if (
            type(self.ordered_set_ids) is not tuple
            or not self.ordered_set_ids
            or any(not isinstance(v, UUID) for v in self.ordered_set_ids)
        ):
            raise ValueError("Draw set order must be a non-empty tuple of UUIDs")
        if len(set(self.ordered_set_ids)) != len(self.ordered_set_ids):
            raise ValueError("Draw set order must be unique")
        if type(self.group_sizes) is not tuple or not self.group_sizes:
            raise ValueError("Group sizes must be a non-empty tuple")
        for size in self.group_sizes:
            if type(size) is not int or size not in ALLOWED_GROUP_SIZES:
                raise ValueError("Group sizes must be powers of two 1..64")
        if sum(self.group_sizes) != len(self.ordered_set_ids):
            raise ValueError("Group sizes must sum to the set count")
        if type(self.group_ids) is not tuple or len(self.group_ids) != len(self.group_sizes):
            raise ValueError("Group IDs must match group sizes")
        if any(not isinstance(v, UUID) for v in self.group_ids):
            raise ValueError("Group IDs must be UUIDs")
        if len(set(self.group_ids)) != len(self.group_ids):
            raise ValueError("Group IDs must be unique")


@dataclass(frozen=True)
class ChampionsGroupsResult:
    """Immutable qualification plus per-tier groups for a future receipt."""

    policy_version: str
    cohort_key: str
    seed: int
    groups: tuple[ChampionsGroup, ...]
    draws: tuple[ChampionsTierDraw, ...]

    def __post_init__(self) -> None:
        if self.policy_version != CHAMPIONS_GROUPS_POLICY:
            raise ValueError("Invalid champions-groups policy")
        if (
            type(self.cohort_key) is not str
            or not self.cohort_key.strip()
            or len(self.cohort_key) > 128
        ):
            raise ValueError("Invalid cohort key")
        if type(self.seed) is not int or not 0 <= self.seed < 2**63:
            raise ValueError("Seed must fit nonnegative BIGINT")
        if type(self.groups) is not tuple or not self.groups:
            raise ValueError("Groups must be a non-empty tuple")
        for group in self.groups:
            if not isinstance(group, ChampionsGroup):
                raise ValueError("Groups must be ChampionsGroup rows")
        if type(self.draws) is not tuple or len(self.draws) != 5:
            raise ValueError("Draws must cover tiers 1..5")
        for draw in self.draws:
            if not isinstance(draw, ChampionsTierDraw):
                raise ValueError("Draws must be ChampionsTierDraw rows")
        if sorted(draw.tier for draw in self.draws) != [1, 2, 3, 4, 5]:
            raise ValueError("Draws must cover tiers 1..5")
        group_ids = [group.id for group in self.groups]
        if len(set(group_ids)) != len(group_ids):
            raise ValueError("Group IDs must be unique")
        # Per-tier cross-check: draws and groups must agree on sets and IDs.
        by_tier_groups: dict[int, list[ChampionsGroup]] = {tier: [] for tier in range(1, 6)}
        for group in self.groups:
            by_tier_groups[group.tier].append(group)
        for draw in self.draws:
            tier_groups = sorted(by_tier_groups[draw.tier], key=lambda g: g.group_index)
            if [g.id for g in tier_groups] != list(draw.group_ids):
                raise ValueError("Tier group IDs differ from the draw")
            if tuple(len(g.league_set_ids) for g in tier_groups) != draw.group_sizes:
                raise ValueError("Tier group sizes differ from the draw")
            union: set[UUID] = set()
            for group in tier_groups:
                union.update(group.league_set_ids)
            if union != set(draw.ordered_set_ids):
                raise ValueError("Tier groups differ from the drawn set order")
            indices = [group.group_index for group in tier_groups]
            if indices != list(range(len(tier_groups))):
                raise ValueError("Tier group indices must run 0..K-1")
            tier_clubs = [p.club_id for group in tier_groups for p in group.participants]
            if len(set(tier_clubs)) != len(tier_clubs):
                raise ValueError("Tier clubs must be unique across its groups")


def split_group_sizes(total_sets: int) -> tuple[int, ...]:
    """Split N compatible sets into power-of-two groups of at most 64.

    :param total_sets: number of compatible league sets (``>= 1``).
    :returns: group sizes in non-increasing order, e.g. 7 -> ``(4, 2, 1)``.
    :raises ValueError: on non-int (``bool`` rejected) or ``< 1``.
    """
    if type(total_sets) is not int or total_sets < 1:
        raise ValueError("Total sets must be a positive int")
    sizes: list[int] = []
    remaining = total_sets
    while remaining > 0:
        if remaining <= MAX_SETS_PER_GROUP:
            take = _largest_power_of_two_at_most(remaining)
            sizes.append(take)
            remaining -= take
        else:
            sizes.append(MAX_SETS_PER_GROUP)
            remaining -= MAX_SETS_PER_GROUP
    return tuple(sizes)


def rounds_for(participant_count: int) -> int:
    """Return ``log2(participants)`` for 8/16/32/64/128/256/512 clubs."""
    if type(participant_count) is not int or participant_count not in ALLOWED_PARTICIPANT_COUNTS:
        raise ValueError("Participant count must be 8/16/32/64/128/256/512")
    return participant_count.bit_length() - 1


def match_days_for(participant_count: int) -> int:
    """Return ``2*log2(participants)-1`` match days (5/7/9/11/13/15/17)."""
    return 2 * rounds_for(participant_count) - 1


def champions_cycles_for(group_size: int) -> tuple[int, ...]:
    """Return structural champions cycles ``1..D`` for a group of G sets."""
    if type(group_size) is not int or group_size not in ALLOWED_GROUP_SIZES:
        raise ValueError("Group size must be a power of two 1..64")
    days = match_days_for(QUALIFIERS_PER_LEAGUE * group_size)
    return tuple(range(1, days + 1))


def free_cycles_for(group_size: int) -> tuple[int, ...]:
    """Return free structural cycles ``D+1..18`` for a group of G sets."""
    if type(group_size) is not int or group_size not in ALLOWED_GROUP_SIZES:
        raise ValueError("Group size must be a power of two 1..64")
    days = match_days_for(QUALIFIERS_PER_LEAGUE * group_size)
    return tuple(range(days + 1, TOTAL_CHAMPIONS_CYCLES + 1))


def group_id_for(cohort_key: str, tier: int, group_index: int) -> UUID:
    """Return the stable seed-independent group ID for tier/group index."""
    _check_cohort_key(cohort_key)
    _check_tier(tier)
    if type(group_index) is not int or group_index < 0:
        raise ValueError("Group index must be a non-negative int")
    return uuid5(
        CHAMPIONS_NAMESPACE,
        f"{CHAMPIONS_GROUPS_POLICY}:{cohort_key}:tier:{tier}:group:{group_index}",
    )


def participant_id_for(cohort_key: str, tier: int, club_id: UUID) -> UUID:
    """Return the stable seed-independent participant ID for tier/club."""
    _check_cohort_key(cohort_key)
    _check_tier(tier)
    if not isinstance(club_id, UUID):
        raise ValueError("Club ID must be UUID")
    return uuid5(
        CHAMPIONS_NAMESPACE,
        f"{CHAMPIONS_GROUPS_POLICY}:{cohort_key}:tier:{tier}:club:{club_id.hex}",
    )


def _draw_key(cohort_key: str, tier: int) -> str:
    return f"{CHAMPIONS_GROUPS_POLICY}:{cohort_key}:tier:{tier}:sets"


def build_champions_groups(
    standings: Sequence[LeagueSnapshot], *, seed: int
) -> ChampionsGroupsResult:
    """Select top-eight qualifiers per league and group compatible sets.

    :param standings: one :class:`LeagueSnapshot` per league in a compatible
        completed cohort (each set owns tiers 1..5, one season per set, one
        season never in two sets, ten unique clubs per league, unique clubs
        and leagues across the cohort, single ``cohort_key``).
    :param seed: explicit draw seed (nonnegative BIGINT, ``bool`` rejected).
    :returns: immutable result with per-tier groups, participants pinned to
        their qualifying season/tier/place, structural cycles and verifiable
        per-tier draws.
    :raises ValueError: on empty/mixed/duplicated/unbalanced/incomplete
        cohorts or an invalid seed. Missing data is never filled with
        invented clubs.
    """
    _check_seed(seed)
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
        raise ValueError("Mixed cohort keys in one champions draw")
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
    season_sets: dict[UUID, UUID] = {}
    for league_set_id, leagues in by_set.items():
        tiers = sorted(snapshot.tier for snapshot in leagues)
        if tiers != [1, 2, 3, 4, 5]:
            raise ValueError(f"Incomplete league set {league_set_id}: tiers 1..5 are required")
        seasons = {snapshot.season_id for snapshot in leagues}
        if len(seasons) != 1:
            raise ValueError("Each league set must use one frozen season")
        season_id = next(iter(seasons))
        if season_id in season_sets:
            raise ValueError("A season cannot belong to different league sets")
        season_sets[season_id] = league_set_id

    num_sets = len(by_set)
    group_sizes = split_group_sizes(num_sets)
    snapshot_by_set_tier: dict[tuple[UUID, int], LeagueSnapshot] = {
        (snapshot.league_set_id, snapshot.tier): snapshot for snapshot in snapshots
    }

    groups: list[ChampionsGroup] = []
    draws: list[ChampionsTierDraw] = []
    for tier in range(1, 6):
        base = sorted(by_set.keys(), key=lambda value: value.hex)
        ordered = list(base)
        Random(stable_seed(seed, _draw_key(cohort_key, tier))).shuffle(ordered)

        tier_group_ids: list[UUID] = []
        offset = 0
        for group_index, size in enumerate(group_sizes):
            member_sets = ordered[offset : offset + size]
            offset += size
            if len(member_sets) != size:
                raise ValueError("Draw slice differs from group sizes")
            group_id = group_id_for(cohort_key, tier, group_index)
            tier_group_ids.append(group_id)

            collected: list[ChampionsParticipant] = []
            for set_id in member_sets:
                snapshot = snapshot_by_set_tier[(set_id, tier)]
                for place, club_id in enumerate(snapshot.order[:QUALIFIERS_PER_LEAGUE], start=1):
                    collected.append(
                        ChampionsParticipant(
                            participant_id=participant_id_for(cohort_key, tier, club_id),
                            club_id=club_id,
                            source_league_id=snapshot.league_id,
                            source_league_set_id=set_id,
                            qualifying_season_id=snapshot.season_id,
                            qualifying_tier=tier,
                            qualifying_place=place,
                        )
                    )
            collected.sort(key=lambda participant: participant.club_id.hex)
            ordered_sets = tuple(sorted(member_sets, key=lambda value: value.hex))
            participant_count = QUALIFIERS_PER_LEAGUE * size
            rounds = rounds_for(participant_count)
            days = match_days_for(participant_count)
            groups.append(
                ChampionsGroup(
                    id=group_id,
                    tier=tier,
                    group_index=group_index,
                    league_set_ids=ordered_sets,
                    participants=tuple(collected),
                    participant_count=participant_count,
                    rounds=rounds,
                    match_days=days,
                    champions_cycles=champions_cycles_for(size),
                    free_cycles=free_cycles_for(size),
                    phase_day=CHAMPIONS_PHASE_DAY,
                )
            )
        draws.append(
            ChampionsTierDraw(
                tier=tier,
                ordered_set_ids=tuple(ordered),
                group_sizes=group_sizes,
                group_ids=tuple(tier_group_ids),
            )
        )

    groups.sort(key=lambda group: (group.tier, group.group_index))
    draws.sort(key=lambda draw: draw.tier)
    result = ChampionsGroupsResult(
        policy_version=CHAMPIONS_GROUPS_POLICY,
        cohort_key=cohort_key,
        seed=seed,
        groups=tuple(groups),
        draws=tuple(draws),
    )

    # Defensive post-check: no silent loss, no invented clubs, no 9th/10th place.
    for tier in range(1, 6):
        tier_groups = [group for group in result.groups if group.tier == tier]
        tier_clubs = [p.club_id for group in tier_groups for p in group.participants]
        expected: set[UUID] = set()
        excluded: set[UUID] = set()
        for snapshot in by_tier[tier]:
            expected.update(snapshot.order[:QUALIFIERS_PER_LEAGUE])
            excluded.update(snapshot.order[QUALIFIERS_PER_LEAGUE:])
        if set(tier_clubs) != expected:
            raise ValueError("Tier qualifiers differ from frozen top-eight clubs")
        if set(tier_clubs) & excluded:
            raise ValueError("Places 9..10 must not qualify")
        if len(tier_clubs) != QUALIFIERS_PER_LEAGUE * num_sets:
            raise ValueError("Tier qualifier count must equal 8N")
    return result
