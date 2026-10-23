"""Pure specialist sessions with bounded, fractional age-season development."""

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from goaletto.domain.development import SKILLS as SKILLS
from goaletto.domain.development import GrowthBudget, grant_growth
from goaletto.domain.match_types import POSITIONS as VALID_POSITIONS
from goaletto.domain.staff_effects import mentor_session, seeded_draw

PROGRESS_KEYS = (*SKILLS, "experience")
POLICY_VERSION = "training-v1"  # Persistent potential/fraction schema, unchanged.
SESSION_POLICY = "training-specialists-v2"


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
    Focus.DEFENDING: frozenset({"DC", "DL", "DR", "DM"}),
    Focus.PLAYMAKING: frozenset({"DM", "MC", "AMC"}),
    Focus.WINGER: frozenset({"ML", "MR", "DL", "DR", "AML", "AMR"}),
    Focus.ATTACKING: frozenset({"FC", "AMC", "AML", "AMR"}),
}
SPECIALISTS = {
    Focus.GOALKEEPING: "goalkeeping_coach",
    Focus.DEFENDING: "defence_coach",
    Focus.ATTACKING: "attack_coach",
    Focus.PLAYMAKING: "attack_coach",
    Focus.WINGER: "attack_coach",
    Focus.SET_PIECES: "attack_coach",
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


def available_focus(focus: Focus, qualifications: dict[str, int]) -> bool:
    if not isinstance(focus, Focus) or any(
        type(v) is not int or not 1 <= v <= 20 for v in qualifications.values()
    ):
        raise ValueError("Invalid training staff")
    return focus == Focus.TEAM or SPECIALISTS[focus] in qualifications


PRIMARY_SKILLS = {
    "GK": "goalkeeping",
    "DC": "defending",
    "DL": "defending",
    "DR": "defending",
    "DM": "defending",
    "MC": "playmaking",
    "ML": "winger",
    "MR": "winger",
    "AML": "winger",
    "AMR": "winger",
    "AMC": "attacking",
    "FC": "attacking",
}


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
    morale: int = 50
    leadership: int = 10
    leadership_progress: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID) or self.position not in VALID_POSITIONS:
            raise ValueError("Invalid training player")
        age_multiplier(self.age)
        if any(
            type(v) is not int for v in (self.morale, self.leadership, self.leadership_progress)
        ) or not (
            0 <= self.morale <= 100
            and 1 <= self.leadership <= 20
            and 0 <= self.leadership_progress < 10000
        ):
            raise ValueError("Invalid morale/leadership")
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
            or not 1 <= self.form <= 10
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
    budget: GrowthBudget
    morale: int
    leadership: int
    leadership_progress: int
    leadership_gain: int


def train_player(
    player: TrainingPlayer,
    focus: Focus,
    intensity: Intensity,
    *,
    seed: str,
    trainer_skill: int,
    head_skill: int = 0,
    physio_skill: int = 0,
    executor_id: UUID | None = None,
    budget: GrowthBudget | None = None,
) -> TrainingResult:
    if not isinstance(focus, Focus) or not isinstance(intensity, Intensity):
        raise ValueError("Invalid training choice")
    if (
        any(
            type(v) is not int or not 0 <= v <= 20
            for v in (trainer_skill, head_skill, physio_skill)
        )
        or type(seed) is not str
        or not seed
    ):
        raise ValueError("Invalid training staff or seed")
    if focus != Focus.TEAM and trainer_skill == 0:
        raise ValueError("Specialist required")
    budget = budget or GrowthBudget(player.age)
    if budget.age != player.age:
        raise ValueError("Development budget age differs from player")
    targeted = focus in (Focus.PHYSICAL, Focus.TEAM) or player.position in POSITIONS.get(focus, ())
    if focus == Focus.SET_PIECES:
        targeted = player.id == executor_id
    trained = targeted and not player.injured
    strength = 5000 + trainer_skill * 500
    gain = (
        (
            (60 if focus == Focus.TEAM else 180)
            * strength
            * age_multiplier(player.age)
            * {Intensity.LOW: 6000, Intensity.MEDIUM: 10000, Intensity.HIGH: 13000}[intensity]
            // 10000**3
        )
        if trained
        else 0
    )
    keys = (
        ("stamina", "pace")
        if focus == Focus.PHYSICAL
        else (PRIMARY_SKILLS[player.position],)
        if focus == Focus.TEAM
        else (focus.value,)
    )
    requested = {
        key: gain // len(keys) + (1 if index < gain % len(keys) else 0)
        for index, key in enumerate(keys)
    }
    growth = grant_growth(
        player.skills, player.caps, player.progress, budget, requested, source="training"
    )
    progress = dict(growth.progress)
    experience = player.experience
    form = player.form
    morale, leadership, fraction, leadership_gain = (
        player.morale,
        player.leadership,
        player.leadership_progress,
        0,
    )
    budget = growth.budget
    if trained:
        experience_gain = (10 + trainer_skill) * age_multiplier(player.age) // 10000
        whole, progress["experience"] = divmod(progress["experience"] + experience_gain, 10000)
        experience += whole
        mentoring = mentor_session(
            player.id, morale, leadership, fraction, budget, head_skill=head_skill, seed=seed
        )
        morale, leadership, fraction, leadership_gain, budget = (
            mentoring.morale,
            mentoring.leadership,
            mentoring.leadership_progress,
            mentoring.leadership_gain,
            mentoring.budget,
        )
        if seeded_draw(seed, player.id, "form", policy=SESSION_POLICY) < (
            200 if intensity == Intensity.LOW else 100
        ):
            form = min(10, form + 1)
    injury_rate = (
        (14 * (10000 - physio_skill * 150) + 9999) // 10000 if intensity == Intensity.HIGH else 0
    )
    injured = (
        trained and seeded_draw(seed, player.id, "injury", policy=SESSION_POLICY) < injury_rate
    )
    return TrainingResult(
        growth.skills,
        progress,
        form,
        experience,
        injured,
        trained,
        sum(growth.granted.values()),
        budget,
        morale,
        leadership,
        fraction,
        leadership_gain,
    )
