"""Pure academy intakes: bounded youth candidates with seeded, persisted draws."""

from dataclasses import dataclass
from random import Random
from uuid import UUID, uuid5

from goaletto.domain.development import SKILLS
from goaletto.domain.finances import PRIMARY
from goaletto.domain.match_types import POSITIONS
from goaletto.domain.names import international_catalog
from goaletto.domain.player_positions import alternative_positions
from goaletto.domain.world import SKILL_NAMES, stable_seed

POLICY = "academy-v1"
MIN_LEVEL = 1
MAX_LEVEL = 5
CANDIDATE_AGE = 17

# Upkeep is charged per four-day finance cycle, adapted from the GDD weekly rates
# (10/20/35/55/80k per week over a ~10-week season map to these cycle values).
_UPKEEP = {1: 6_000, 2: 12_000, 3: 20_000, 4: 32_000, 5: 46_000}
# One-time cost to reach each level; level 1 is the starting academy.
_UPGRADE_COST = {2: 200_000, 3: 500_000, 4: 1_000_000, 5: 2_500_000}
# Graduates a manager may sign per season, and the skill band of its candidates.
_SIGN_LIMIT = {1: 1, 2: 2, 3: 2, 4: 3, 5: 3}
_SKILL_BAND = {1: (3, 7), 2: (4, 9), 3: (5, 11), 4: (6, 13), 5: (7, 15)}
# 60%..90%..100% adjacent-position draw is shared with new market people.
_CANDIDATE_POSITIONS = tuple(sorted(POSITIONS))


@dataclass(frozen=True)
class AcademyCandidate:
    person_id: UUID
    name: str
    age: int
    position: str
    alternatives: tuple[str, ...]
    skills: tuple[int, ...]
    caps: dict[str, int]


def _check_level(level: int) -> int:
    if type(level) is not int or not MIN_LEVEL <= level <= MAX_LEVEL:
        raise ValueError("Invalid academy level")
    return level


def academy_upkeep(level: int) -> int:
    return _UPKEEP[_check_level(level)]


def upgrade_cost(level: int) -> int:
    """Cost to move from ``level`` to the next level; maxed academies have none."""
    _check_level(level)
    if level == MAX_LEVEL:
        raise ValueError("Academy is already at maximum level")
    return _UPGRADE_COST[level + 1]


def sign_limit(level: int) -> int:
    return _SIGN_LIMIT[_check_level(level)]


def candidate_count(level: int) -> int:
    """Every level offers a shortlist of three to five candidates."""
    return sign_limit(level) + 2


def skill_band(level: int) -> tuple[int, int]:
    return _SKILL_BAND[_check_level(level)]


def candidate(club_id: UUID, season_id: UUID, slot: int, level: int) -> AcademyCandidate:
    """Deterministic candidate for one intake slot; retries reproduce the same person."""
    _check_level(level)
    if not isinstance(club_id, UUID) or not isinstance(season_id, UUID):
        raise ValueError("Academy intake needs club and season identities")
    if type(slot) is not int or not 1 <= slot <= candidate_count(level):
        raise ValueError("Invalid academy intake slot")
    seed = stable_seed(0, f"{POLICY}:{club_id}:{season_id}:{slot}")
    rng = Random(seed)
    position = _CANDIDATE_POSITIONS[rng.randrange(len(_CANDIDATE_POSITIONS))]
    low, high = _SKILL_BAND[level]
    values = [rng.randint(low, high) for _ in SKILLS]
    primary = PRIMARY[position]
    values[primary] = min(20, max(values[primary], high))
    if position != "GK":
        values[0] = rng.randint(1, 3)
    leadership = rng.randint(low, min(20, high + 3))
    loyalty = rng.randint(low, min(20, high + 5))
    skills = (*values, leadership, loyalty)
    headroom = 2 + level
    caps = {
        skill: min(20, value + rng.randint(1, headroom))
        for skill, value in zip(SKILLS, values, strict=True)
    }
    caps[SKILLS[primary]] = min(20, caps[SKILLS[primary]] + level)
    catalog = international_catalog(seed)
    _, name = catalog[seed % len(catalog)]
    person_id = _person_id(club_id, season_id, slot)
    return AcademyCandidate(
        person_id=person_id,
        name=name,
        age=CANDIDATE_AGE,
        position=position,
        alternatives=alternative_positions(person_id, position),
        skills=tuple(skills),
        caps=caps,
    )


def _person_id(club_id: UUID, season_id: UUID, slot: int) -> UUID:
    return uuid5(club_id, f"{POLICY}:{season_id}:{slot}")


def skill_snapshot(skills: tuple[int, ...]) -> dict[str, int]:
    """Map the ten-value candidate tuple onto the persisted skill names."""
    if len(skills) != len(SKILL_NAMES) or any(
        type(value) is not int or not 1 <= value <= 20 for value in skills
    ):
        raise ValueError("Invalid academy skills")
    return dict(zip(SKILL_NAMES, skills, strict=True))
