"""Pure pre-match lineup validation. No database, clock reads or network calls."""

from dataclasses import dataclass
from uuid import UUID

from goaletto.domain.match_types import POSITIONS, Mentality, Tactic

FORMATION_SHAPES: dict[str, tuple[int, int, int]] = {
    "3-4-3": (3, 4, 3),
    "3-5-2": (3, 5, 2),
    "4-3-3": (4, 3, 3),
    "4-4-2": (4, 4, 2),
    "4-5-1": (4, 5, 1),
    "5-3-2": (5, 3, 2),
    "5-4-1": (5, 4, 1),
}

DEF_POSITIONS = frozenset({"DL", "DC", "DR"})
MID_POSITIONS = frozenset({"DM", "ML", "MC", "MR"})
ATT_POSITIONS = frozenset({"AMC", "AML", "AMR", "FC"})

FORMATION_SLOTS: dict[str, tuple[str, ...]] = {
    "3-4-3": ("GK", "DC", "DC", "DC", "ML", "MC", "MC", "MR", "AML", "FC", "AMR"),
    "3-5-2": ("GK", "DC", "DC", "DC", "ML", "DM", "MC", "MC", "MR", "FC", "FC"),
    "4-3-3": ("GK", "DL", "DC", "DC", "DR", "DM", "MC", "MC", "AML", "FC", "AMR"),
    "4-4-2": ("GK", "DL", "DC", "DC", "DR", "ML", "MC", "MC", "MR", "FC", "FC"),
    "4-5-1": ("GK", "DL", "DC", "DC", "DR", "ML", "MC", "MC", "MC", "MR", "FC"),
    "5-3-2": ("GK", "DL", "DC", "DC", "DC", "DR", "MC", "MC", "MC", "FC", "FC"),
    "5-4-1": ("GK", "DL", "DC", "DC", "DC", "DR", "ML", "MC", "MC", "MR", "FC"),
}

PLAYER_STATUSES = frozenset({"active", "last_season", "retired"})


class LineupInvalid(ValueError):
    """Domain rejection with a stable machine-readable code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True)
class RosterPlayer:
    person_id: UUID
    position: str
    alternatives: tuple[str, ...]
    status: str
    injured: bool

    def __post_init__(self) -> None:
        if not isinstance(self.person_id, UUID):
            raise ValueError("Roster identity must be a UUID")
        if self.position not in POSITIONS or any(p not in POSITIONS for p in self.alternatives):
            raise ValueError("Unknown roster position")
        if self.status not in PLAYER_STATUSES:
            raise ValueError("Unknown player status")
        if type(self.injured) is not bool:
            raise ValueError("Injury flag must be a boolean")


@dataclass(frozen=True)
class StarterPick:
    person_id: UUID
    slot_position: str

    def __post_init__(self) -> None:
        if not isinstance(self.person_id, UUID) or self.slot_position not in POSITIONS:
            raise ValueError("Invalid starter pick")


@dataclass(frozen=True)
class ValidatedLineup:
    formation: str
    starters: tuple[StarterPick, ...]
    bench: tuple[UUID, ...]
    captain_id: UUID
    tactic: Tactic
    mentality: Mentality
    warnings: tuple[str, ...]


def _shape_of(slots: tuple[StarterPick, ...]) -> tuple[int, int, int]:
    defenders = sum(p.slot_position in DEF_POSITIONS for p in slots)
    midfield = sum(p.slot_position in MID_POSITIONS for p in slots)
    attackers = sum(p.slot_position in ATT_POSITIONS for p in slots)
    return defenders, midfield, attackers


def validate_lineup(
    *,
    roster: tuple[RosterPlayer, ...],
    starters: tuple[StarterPick, ...],
    bench: tuple[UUID, ...],
    formation: str,
    captain_id: UUID,
    tactic: Tactic | str,
    mentality: Mentality | str,
) -> ValidatedLineup:
    """Validate a pre-match XI/bench/captain/tactics pick against a fresh roster.

    The roster must already reflect current eligibility: only non-retired club
    players, with injuries resolved by the caller against an explicit timestamp.
    A warned last-season footballer still plays; a retired one never does.
    """
    if formation not in FORMATION_SHAPES:
        raise LineupInvalid("formation", f"Unsupported formation: {formation}")
    try:
        tactic_value = tactic if isinstance(tactic, Tactic) else Tactic(tactic)
    except ValueError:
        raise LineupInvalid("tactic", f"Unknown tactic: {tactic}") from None
    try:
        mentality_value = mentality if isinstance(mentality, Mentality) else Mentality(mentality)
    except ValueError:
        raise LineupInvalid("mentality", f"Unknown mentality: {mentality}") from None
    if not isinstance(captain_id, UUID):
        raise LineupInvalid("captain", "Captain identity must be a UUID")
    if len(starters) != 11:
        raise LineupInvalid("count", "The starting XI needs exactly eleven players")
    if any(not isinstance(p, StarterPick) for p in starters):
        raise LineupInvalid("count", "Invalid starter pick")
    if any(not isinstance(p, UUID) for p in bench):
        raise LineupInvalid("count", "Invalid bench identity")
    if len(bench) > 9:
        raise LineupInvalid("count", "The bench holds at most nine players")

    eligible = {p.person_id: p for p in roster if p.status != "retired"}
    if len(eligible) != len(roster):
        raise LineupInvalid("retired", "Retired players cannot be selected")
    if len(eligible) != len({p.person_id for p in roster}):
        raise LineupInvalid("duplicate", "Roster identities must be distinct")
    picked = [p.person_id for p in starters] + list(bench)
    if len(set(picked)) != len(picked):
        raise LineupInvalid("duplicate", "XI and bench must not repeat a player")
    if set(picked) != set(eligible):
        unknown = [p for p in picked if p not in eligible]
        if unknown:
            raise LineupInvalid("foreign", "XI and bench must be current club players")
        raise LineupInvalid("coverage", "XI and bench must cover the eligible roster")

    if sum(p.slot_position == "GK" for p in starters) != 1:
        raise LineupInvalid("goalkeeper", "The XI needs exactly one goalkeeper slot")
    for pick in starters:
        player = eligible[pick.person_id]
        if pick.slot_position == "GK":
            if player.position != "GK":
                raise LineupInvalid(
                    "goalkeeper_assignment", "Only a goalkeeper plays the goalkeeper slot"
                )
        elif player.position == "GK":
            raise LineupInvalid(
                "goalkeeper_assignment", "A goalkeeper cannot play an outfield slot"
            )
        if player.injured:
            raise LineupInvalid("injured", "An injured player cannot start")

    starter_ids = {p.person_id for p in starters}
    if captain_id not in starter_ids:
        raise LineupInvalid("captain", "The captain must be one of the eleven starters")

    expected = FORMATION_SHAPES[formation]
    actual = _shape_of(starters)
    if actual != expected:
        raise LineupInvalid(
            "shape",
            f"Formation {formation} needs {expected[0]}-{expected[1]}-{expected[2]}, "
            f"got {actual[0]}-{actual[1]}-{actual[2]}",
        )

    warnings: list[str] = []
    for pick in starters:
        player = eligible[pick.person_id]
        if player.status == "last_season":
            warnings.append(f"last_season_starter:{pick.person_id}")
        if pick.slot_position != player.position and pick.slot_position not in player.alternatives:
            warnings.append(f"off_position:{pick.person_id}")
    for person_id in bench:
        if eligible[person_id].injured:
            warnings.append(f"injured_on_bench:{person_id}")
        elif eligible[person_id].status == "last_season":
            warnings.append(f"last_season_bench:{person_id}")
    return ValidatedLineup(
        formation=formation,
        starters=starters,
        bench=tuple(bench),
        captain_id=captain_id,
        tactic=tactic_value,
        mentality=mentality_value,
        warnings=tuple(warnings),
    )


@dataclass(frozen=True)
class SlotCandidate:
    person_id: UUID
    position: str
    alternatives: tuple[str, ...]


def reassign_slots(
    candidates: tuple[SlotCandidate, ...], formation: str
) -> tuple[StarterPick, ...]:
    """Keep the same eleven players, fit them into a new formation template.

    Goalkeeper stays in goal. Outfield players prefer their main position, then
    an alternative, then take the remaining slot with an off-position warning.
    """
    template = FORMATION_SLOTS.get(formation)
    if template is None:
        raise LineupInvalid("formation", f"Unsupported formation: {formation}")
    if len(candidates) != 11 or len({c.person_id for c in candidates}) != 11:
        raise LineupInvalid("count", "Reassignment needs eleven distinct players")
    keepers = [c for c in candidates if c.position == "GK"]
    if len(keepers) != 1:
        raise LineupInvalid("goalkeeper_assignment", "The XI needs exactly one goalkeeper")
    remaining = [c for c in candidates if c.position != "GK"]
    picks = [StarterPick(keepers[0].person_id, "GK")]
    for slot in template[1:]:
        pick = next((c for c in remaining if c.position == slot), None)
        if pick is None:
            pick = next((c for c in remaining if slot in c.alternatives), None)
        if pick is None:
            pick = remaining[0]
        remaining.remove(pick)
        picks.append(StarterPick(pick.person_id, slot))
    return tuple(picks)
