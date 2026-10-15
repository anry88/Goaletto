"""Immutable regulation snapshots, state and commands. No framework dependencies."""

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from goaletto.domain.staff_quality import StaffAbilities

from goaletto.domain.clock import MatchPhase
from goaletto.domain.knockout_match import KnockoutContext

ENGINE_VERSION = "regulation-v1"
LIVE_ENGINE_VERSION = "regulation-v2"
COACH_ENGINE_VERSION = "regulation-v3"
KNOCKOUT_ENGINE_VERSION = "regulation-v4"
POSITIONS = frozenset({"GK", "DL", "DC", "DR", "DM", "ML", "MC", "MR", "AMC", "AML", "AMR", "FC"})


class Side(StrEnum):
    HOME = "home"
    AWAY = "away"

    @property
    def opponent(self) -> "Side":
        return Side.AWAY if self == Side.HOME else Side.HOME


class Tactic(StrEnum):
    NORMAL = "normal"
    PRESSING = "pressing"
    COUNTER = "counter"
    WINGS = "wings"
    DEFENCE = "defence"
    LONG_BALLS = "long_balls"


class Mentality(StrEnum):
    ULTRA_ATTACK = "ultra_attack"
    ATTACK = "attack"
    BALANCED = "balanced"
    DEFENCE = "defence"
    ULTRA_DEFENCE = "ultra_defence"


class Instruction(StrEnum):
    LONG_SHOTS = "long_shots"
    MOVE_LEFT = "move_left"
    MOVE_RIGHT = "move_right"
    DROP_BACK = "drop_back"
    RUNS_BEHIND = "runs_behind"


@dataclass(frozen=True)
class MatchSkills:
    goalkeeping: int
    defending: int
    playmaking: int
    winger: int
    attacking: int
    set_pieces: int
    stamina: int
    pace: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or not 1 <= value <= 20
            for value in (
                self.goalkeeping,
                self.defending,
                self.playmaking,
                self.winger,
                self.attacking,
                self.set_pieces,
                self.stamina,
                self.pace,
            )
        ):
            raise ValueError("Match skills must be integers from 1 through 20")


@dataclass(frozen=True)
class PlayerSnapshot:
    id: UUID
    name: str
    position: str
    skills: MatchSkills
    age: int = 25
    form: int = 6
    experience: int = 0
    alternatives: tuple[str, ...] = ()
    available: bool = True
    last_season: bool = False
    appearance_identity_id: str | None = None
    appearance_variant_id: str | None = None
    catalog_version: int | None = None
    morale: int = 50

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID) or not self.name.strip():
            raise ValueError("Player needs stable identity and name")
        if self.position not in POSITIONS or any(p not in POSITIONS for p in self.alternatives):
            raise ValueError("Unknown player position")
        if len(self.alternatives) > 2 or len(set(self.alternatives)) != len(self.alternatives):
            raise ValueError("At most two distinct alternative positions")
        if self.position in self.alternatives:
            raise ValueError("Alternative position repeats main position")
        if type(self.form) is not int or not 1 <= self.form <= 8:
            raise ValueError("Form must be an integer from 1 through 8")
        if type(self.morale) is not int or not 0 <= self.morale <= 100:
            raise ValueError("Morale must be an integer from 0 through 100")
        if type(self.age) is not int or not 17 <= self.age <= 38:
            raise ValueError("An active footballer must be 17 through 38")
        if type(self.experience) is not int or self.experience < 0:
            raise ValueError("Experience must be nonnegative")
        if type(self.available) is not bool or type(self.last_season) is not bool:
            raise ValueError("Player eligibility flags must be booleans")
        appearance = (self.appearance_identity_id, self.appearance_variant_id, self.catalog_version)
        if any(value is not None for value in appearance):
            if not all(value is not None for value in appearance):
                raise ValueError("Appearance metadata must be complete")
            if type(self.catalog_version) is not int or self.catalog_version < 1:
                raise ValueError("Unsupported appearance catalog version")


@dataclass(frozen=True)
class FieldSlot:
    player_id: UUID
    position: str

    def __post_init__(self) -> None:
        if not isinstance(self.player_id, UUID) or self.position not in POSITIONS:
            raise ValueError("Invalid field slot")


@dataclass(frozen=True)
class CoachSnapshot:
    person_id: UUID | None
    skill: int = 1
    policy_version: str = "staff-quality-v1"

    def __post_init__(self) -> None:
        if self.person_id is not None and not isinstance(self.person_id, UUID):
            raise ValueError("Invalid coach identity")
        if type(self.skill) is not int or not 1 <= self.skill <= 20:
            raise ValueError("Invalid coach skill")
        if self.person_id is None and self.skill != 1:
            raise ValueError("Vacancy must use basic coaching")
        if self.policy_version != "staff-quality-v1":
            raise ValueError("Unknown coaching policy")

    @property
    def abilities(self) -> "StaffAbilities":
        from goaletto.domain.staff_quality import staff_abilities

        return staff_abilities("head_coach", self.skill, self.policy_version)


@dataclass(frozen=True)
class TeamSnapshot:
    club_id: UUID
    name: str
    players: tuple[PlayerSnapshot, ...]
    lineup: tuple[FieldSlot, ...]
    bench: tuple[UUID, ...]
    tactic: Tactic = Tactic.NORMAL
    mentality: Mentality = Mentality.BALANCED
    automatic_injury_substitutions: bool = True
    coach: CoachSnapshot | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.club_id, UUID) or not self.name.strip():
            raise ValueError("Team needs stable identity and name")
        if not 11 <= len(self.players) <= 20 or len(self.lineup) != 11:
            raise ValueError("Regulation needs eleven starters and at most twenty players")
        ids = [p.id for p in self.players]
        selected = [slot.player_id for slot in self.lineup] + list(self.bench)
        if len(set(ids)) != len(ids) or len(set(selected)) != len(selected):
            raise ValueError("Player identities must be distinct within the team")
        if set(selected) != set(ids):
            raise ValueError("Every snapshot player must be in the lineup or bench")
        if sum(slot.position == "GK" for slot in self.lineup) != 1:
            raise ValueError("Lineup needs exactly one goalkeeper slot")
        if any(not p.available for p in self.players if p.id in {s.player_id for s in self.lineup}):
            raise ValueError("Unavailable player cannot start")
        if not isinstance(self.tactic, Tactic) or not isinstance(self.mentality, Mentality):
            raise ValueError("Unknown tactic or mentality")
        if type(self.automatic_injury_substitutions) is not bool:
            raise ValueError("Automatic substitution policy must be a boolean")


@dataclass(frozen=True)
class HomeCrowd:
    stadium_capacity: int = 0
    home_fans: int = 0

    def __post_init__(self) -> None:
        if (
            type(self.stadium_capacity) is not int
            or type(self.home_fans) is not int
            or not 0 <= self.home_fans <= self.stadium_capacity
        ):
            raise ValueError("Home attendance must fit the stadium capacity")


@dataclass(frozen=True)
class EnginePolicy:
    version: str = "match-policy-v1"
    base_chance_rate_bps: int = 8000  # Lambda 0.8 per 3-game-minute tick, ~24 per match.
    yellow_bps: int = 300
    direct_red_bps: int = 30
    injury_bps: int = 100
    foul_bps: int = 1800
    corner_bps: int = 800
    max_substitutions: int = 5
    max_windows: int = 3
    max_tactic_changes: int = 3
    minimum_players: int = 7
    max_support_morale: int = 12
    full_support_fans: int = 10000
    max_instruction_changes: int = 3
    instruction_bonus_bps: int = 1500
    long_shot_extra_miss_bps: int = 1000

    def __post_init__(self) -> None:
        if self.version not in {
            "match-policy-v1",
            "match-policy-v2",
            "match-policy-v3",
            "match-policy-v4",
        }:
            raise ValueError("Unsupported policy")
        if self.version == "match-policy-v1" and (
            self.max_instruction_changes,
            self.instruction_bonus_bps,
            self.long_shot_extra_miss_bps,
        ) != (3, 1500, 1000):
            raise ValueError("Legacy policy cannot carry instruction tuning")
        if self.max_instruction_changes != 3 or type(self.max_instruction_changes) is not int:
            raise ValueError("Instruction limit must be three")
        for value in (self.instruction_bonus_bps, self.long_shot_extra_miss_bps):
            if type(value) is not int or not 0 <= value <= 3000:
                raise ValueError("Instruction modifiers must be bounded basis points")
        if type(self.max_support_morale) is not int or not 0 <= self.max_support_morale <= 20:
            raise ValueError("Support morale must be an integer from 0 through 20")
        if type(self.full_support_fans) is not int or self.full_support_fans < 1:
            raise ValueError("Full support requires a positive fan count")
        for value in (
            self.base_chance_rate_bps,
            self.yellow_bps,
            self.direct_red_bps,
            self.injury_bps,
            self.foul_bps,
            self.corner_bps,
        ):
            if type(value) is not int or not 0 <= value <= 10000:
                raise ValueError("Policy probabilities must be bounded integer basis points")
        if (
            self.max_substitutions,
            self.max_windows,
            self.max_tactic_changes,
            self.minimum_players,
        ) != (5, 3, 3, 7):
            raise ValueError("Unsupported regulation limits")
        if any(
            type(value) is not int
            for value in (
                self.max_substitutions,
                self.max_windows,
                self.max_tactic_changes,
                self.minimum_players,
            )
        ):
            raise ValueError("Regulation limits must be integers")


@dataclass(frozen=True)
class PlayerCondition:
    player_id: UUID
    energy_bps: int = 10000
    minutes_played: int = 0
    yellows: int = 0
    dismissed: bool = False
    injured: bool = False


@dataclass(frozen=True)
class TeamStats:
    goals: int = 0
    chances: int = 0
    shots: int = 0
    on_target: int = 0
    saves: int = 0
    interceptions: int = 0
    corners: int = 0
    fouls: int = 0
    possession_units: int = 0


@dataclass(frozen=True)
class PlayerInstruction:
    player_id: UUID
    instruction: Instruction

    def __post_init__(self) -> None:
        if not isinstance(self.player_id, UUID) or not isinstance(self.instruction, Instruction):
            raise ValueError("Invalid individual instruction")


@dataclass(frozen=True)
class TeamState:
    snapshot: TeamSnapshot
    lineup: tuple[FieldSlot, ...]
    bench: tuple[UUID, ...]
    removed: tuple[UUID, ...]
    conditions: tuple[PlayerCondition, ...]
    stats: TeamStats
    tactic: Tactic
    mentality: Mentality
    substitutions: int = 0
    substitution_windows: tuple[int, ...] = ()
    tactic_changes: int = 0
    counter_ready: bool = False
    support_morale_points: int = 0
    instructions: tuple[PlayerInstruction, ...] = ()
    instruction_changes: int = 0
    mentality_changes: int = 0


@dataclass(frozen=True)
class MatchState:
    match_id: UUID
    seed: int
    home: TeamState
    away: TeamState
    policy: EnginePolicy
    crowd: HomeCrowd = HomeCrowd()
    tick: int = 0
    game_minute: int = 0
    phase: MatchPhase = MatchPhase.FIRST_HALF
    applied_commands: tuple[UUID, ...] = ()
    engine_version: str = ENGINE_VERSION
    termination_reason: str | None = None
    knockout: KnockoutContext | None = None


@dataclass(frozen=True)
class MatchEvent:
    id: UUID
    kind: str
    tick: int
    minute: int
    side: Side | None = None
    player_id: UUID | None = None
    other_player_id: UUID | None = None
    detail: str | None = None


class CommandKind(StrEnum):
    SUBSTITUTION = "substitution"
    TACTIC = "tactic"
    MENTALITY = "mentality"
    INSTRUCTION = "instruction"


@dataclass(frozen=True)
class MatchCommand:
    id: UUID
    side: Side
    target_tick: int
    kind: CommandKind
    outgoing_id: UUID | None = None
    incoming_id: UUID | None = None
    tactic: Tactic | None = None
    mentality: Mentality | None = None
    halftime: bool = False
    player_id: UUID | None = None
    instruction: Instruction | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID) or not isinstance(self.side, Side):
            raise ValueError("Command needs stable ID and side")
        if type(self.target_tick) is not int or not 1 <= self.target_tick <= 40:
            raise ValueError("Command target must be tick 1 through 40")
        if not isinstance(self.kind, CommandKind):
            raise ValueError("Unknown match command")
        if type(self.halftime) is not bool:
            raise ValueError("Command window must be explicit")
        if self.kind == CommandKind.INSTRUCTION:
            if (
                not isinstance(self.player_id, UUID)
                or not isinstance(self.instruction, Instruction)
                or any(
                    x is not None
                    for x in (self.outgoing_id, self.incoming_id, self.tactic, self.mentality)
                )
            ):
                raise ValueError("Instruction needs one player and a closed instruction")
            return
        if self.player_id is not None or self.instruction is not None:
            raise ValueError("Unrelated instruction fields")
        if any(
            value is not None and not isinstance(value, UUID)
            for value in (self.outgoing_id, self.incoming_id)
        ):
            raise ValueError("Command player references must be UUIDs")
        if self.kind == CommandKind.SUBSTITUTION:
            if (
                self.outgoing_id is None
                or self.incoming_id is None
                or self.outgoing_id == self.incoming_id
            ):
                raise ValueError("Substitution needs two distinct player IDs")
            if self.tactic is not None or self.mentality is not None:
                raise ValueError("Substitution has unrelated fields")
        elif self.kind == CommandKind.TACTIC:
            if not isinstance(self.tactic, Tactic) or self.mentality is not None:
                raise ValueError("Tactic command needs a tactic")
            if self.outgoing_id is not None or self.incoming_id is not None:
                raise ValueError("Tactic has player fields")
        else:
            if not isinstance(self.mentality, Mentality) or self.tactic is not None:
                raise ValueError("Mentality command needs a mentality")
            if self.outgoing_id is not None or self.incoming_id is not None:
                raise ValueError("Mentality has player fields")


@dataclass(frozen=True)
class TickResult:
    state: MatchState
    events: tuple[MatchEvent, ...]
    accepted_commands: tuple[UUID, ...] = ()
    rejected_commands: tuple[tuple[UUID, str], ...] = ()
