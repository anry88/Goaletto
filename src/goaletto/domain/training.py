"""Pure, versioned weekly development. Integer progress and deterministic draws."""

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from goaletto.domain.match_types import POSITIONS as VALID_POSITIONS

SKILLS = (
    "goalkeeping",
    "defending",
    "playmaking",
    "winger",
    "attacking",
    "set_pieces",
    "stamina",
    "pace",
)
PROGRESS_KEYS = (*SKILLS, "experience")
POLICY_VERSION = "training-v1"


class Focus(StrEnum):
    GOALKEEPING = "goalkeeping"
    DEFENDING = "defending"
    PLAYMAKING = "playmaking"
    WINGER = "winger"
    ATTACKING = "attacking"
    SET_PIECES = "set_pieces"
    PHYSICAL = "physical"
    TEAM = "team"


class Intensity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


POSITIONS = {
    Focus.GOALKEEPING: frozenset({"GK"}),
    Focus.DEFENDING: frozenset({"DC", "DL", "DR"}),
    Focus.PLAYMAKING: frozenset({"MC"}),
    Focus.WINGER: frozenset({"ML", "MR", "DL", "DR"}),
    Focus.ATTACKING: frozenset({"FC", "AMC", "AML", "AMR"}),
}
SPECIALISTS = {
    Focus.GOALKEEPING: "goalkeeping_coach",
    Focus.DEFENDING: "defence_coach",
    Focus.ATTACKING: "attack_coach",
    Focus.PHYSICAL: "fitness_coach",
}


def initial_caps(person_id: UUID, skills: dict[str, int]) -> dict[str, int]:
    """Stable legacy/seed baseline; academy can supply its own caps on creation."""
    if set(skills) != set(SKILLS) or any(
        type(v) is not int or not 1 <= v <= 20 for v in skills.values()
    ):
        raise ValueError("Invalid skills")
    return {
        skill: max(
            value,
            12
            + int(
                hashlib.md5(
                    f"training-caps:v1:{person_id}:{skill}".encode(), usedforsecurity=False
                ).hexdigest()[:8],
                16,
            )
            % 9,
        )
        for skill, value in skills.items()
    }


def age_multiplier(age: int) -> int:
    if type(age) is not int or not 17 <= age <= 150:
        raise ValueError("Invalid training age")
    if age < 22:
        return 15000 - (age - 17) * 600
    if age < 27:
        return 12000 - (age - 22) * 800
    if age < 32:
        return 8000 - (age - 27) * 1000
    return 3000


def coach_bonus(focus: Focus, qualifications: dict[str, int]) -> int:
    if any(type(v) is not int or not 1 <= v <= 20 for v in qualifications.values()):
        raise ValueError("Invalid coach skill")
    main = qualifications.get("head_coach", 0) * 100
    specialist = qualifications.get(SPECIALISTS.get(focus, ""), 0) * 150
    return min(5000, main + specialist)


@dataclass(frozen=True)
class TrainingPlayer:
    id: UUID
    age: int
    position: str
    skills: dict[str, int]
    caps: dict[str, int]
    progress: dict[str, int]
    form: int
    experience: int
    injured: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID) or self.position not in VALID_POSITIONS:
            raise ValueError("Invalid training player")
        age_multiplier(self.age)
        if set(self.skills) != set(SKILLS) or set(self.caps) != set(SKILLS):
            raise ValueError("Invalid development skill keys")
        if set(self.progress) != set(PROGRESS_KEYS):
            raise ValueError("Invalid development progress keys")
        if any(type(v) is not int or not 0 <= v < 10000 for v in self.progress.values()):
            raise ValueError("Invalid fractional progress")
        if any(
            type(self.skills[k]) is not int
            or type(self.caps[k]) is not int
            or not 1 <= self.skills[k] <= self.caps[k] <= 20
            for k in SKILLS
        ):
            raise ValueError("Invalid potential or skill")
        if (
            type(self.form) is not int
            or not 1 <= self.form <= 8
            or type(self.experience) is not int
            or self.experience < 0
        ):
            raise ValueError("Invalid training condition")


@dataclass(frozen=True)
class TrainingResult:
    skills: dict[str, int]
    progress: dict[str, int]
    form: int
    experience: int
    injured: bool
    trained: bool
    gain_bps: int


def train_player(
    player: TrainingPlayer,
    focus: Focus,
    intensity: Intensity,
    *,
    seed: str,
    bonus_bps: int = 0,
    physio_skill: int = 0,
    executor_id: UUID | None = None,
) -> TrainingResult:
    if not isinstance(focus, Focus) or not isinstance(intensity, Intensity):
        raise ValueError("Invalid training choice")
    if (
        type(bonus_bps) is not int
        or not 0 <= bonus_bps <= 5000
        or type(physio_skill) is not int
        or not 0 <= physio_skill <= 20
        or type(seed) is not str
        or not seed
    ):
        raise ValueError("Invalid training policy input")
    targeted = focus in (Focus.PHYSICAL, Focus.TEAM) or player.position in POSITIONS.get(focus, ())
    if focus == Focus.SET_PIECES:
        targeted = player.id == executor_id
    trained = targeted and not player.injured
    skills, progress = dict(player.skills), dict(player.progress)
    form, experience = player.form, player.experience
    gain = (
        4000
        * (10000 + bonus_bps)
        * age_multiplier(player.age)
        * {Intensity.LOW: 6000, Intensity.MEDIUM: 10000, Intensity.HIGH: 13000}[intensity]
        // (7 * 10000**3)
        if trained
        else 0
    )
    keys = (
        ("stamina", "pace")
        if focus == Focus.PHYSICAL
        else ("experience",)
        if focus == Focus.TEAM
        else (focus.value,)
    )
    for key in keys if trained else ():
        before = experience if key == "experience" else skills[key]
        cap = 2**31 - 1 if key == "experience" else player.caps[key]
        whole, fraction = divmod(progress[key] + gain, 10000)
        after = min(cap, before + whole)
        progress[key] = fraction if after < cap else 0
        if key == "experience":
            experience = after
        else:
            skills[key] = after

    def draw(kind: str) -> int:
        digest = hashlib.sha256(f"{POLICY_VERSION}:{seed}:{player.id}:{kind}".encode()).digest()
        return int.from_bytes(digest[:8], "big") % 10000

    # Daily effects preserve the modest weekly baseline across seven free dates.
    if trained and draw("form") < (1500 if intensity == Intensity.LOW else 1000) // 7:
        form = min(8, form + 1)
    injury_rate = 100 * (10000 - physio_skill * 150) // 10000 if intensity == Intensity.HIGH else 0
    injured = trained and draw("injury") < injury_rate // 7
    return TrainingResult(skills, progress, form, experience, injured, trained, gain)
