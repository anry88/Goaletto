"""Match practice for every real game: league, cup, champions and friendlies.

Versioned practice: V4 implements the user's position/performance update.

Official competitions share one development scale identical to
``match-development-v1`` for 0..90 minutes and extended linearly to 120
minutes of total actual play (extra time is the summed minutes, never a
second reward for the first 90 and never a separate full award for an
extra segment; penalty shootouts grant no minutes). Friendlies scale the
official skill and experience gains by explicit integer factors in
1..9999 bps. Form uses a separate seeded probabilistic step, 1..10 in V3, with
no guaranteed +1 and no fast saturation.

Existing ``match-development-v1`` receipts and replay are untouched: this
module neither changes that module nor replays its history. It reuses the
shared ``development-budget-v1`` ``grant_growth`` (source ``match``), the
training age multiplier, the primary-skill map and the profile-specialist
strength so official 90-minute gains keep their current small scale.

Pure domain: no aiogram/SQLAlchemy, no wall clock, no unseeded RNG. The
calculation itself is not exactly-once persistence; Codex pins the
returned terms (policy, competition, factors, staff snapshot, seed,
minutes) in a durable snapshot and applies form/skills/experience/budget
atomically with finalization.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from goaletto.domain.development import GrowthBudget, SkillGrowth, grant_growth
from goaletto.domain.player_form import MatchPerformance, performance_chances
from goaletto.domain.staff_effects import seeded_draw
from goaletto.domain.training import PRIMARY_SKILLS, TrainingPlayer, age_multiplier

POLICY = "match-practice-v1"
V2_POLICY = "match-practice-v2"
V3_POLICY = "match-practice-v3"
CURRENT_POLICY = "match-practice-v4"

# User-approved 2026-10-25; older frozen matches retain 50%.
LEGACY_FRIENDLY_FACTOR = 5000
DEFAULT_FRIENDLY_SKILL_FACTOR = 7500
DEFAULT_FRIENDLY_EXPERIENCE_FACTOR = 7500

MIN_MINUTES = 0
MAX_MINUTES = 120
REGULATION_MINUTES = 90


class Competition(StrEnum):
    LEAGUE = "league"
    CUP = "cup"
    CHAMPIONS = "champions"
    FRIENDLY = "friendly"


OFFICIAL_COMPETITIONS = frozenset({Competition.LEAGUE, Competition.CUP, Competition.CHAMPIONS})

LEGACY_POSITION_ROLES = {
    position: (
        "goalkeeping_coach"
        if position == "GK"
        else "defence_coach"
        if position in {"DC", "DL", "DR", "DM"}
        else "attack_coach"
    )
    for position in PRIMARY_SKILLS
}
POSITION_ROLES = {
    "GK": "goalkeeping_coach",
    "DC": "defence_coach",
    "DL": "defence_coach",
    "DR": "defence_coach",
    "DM": "midfield_coach",
    "MC": "midfield_coach",
    "AMC": "midfield_coach",
    "ML": "wing_coach",
    "MR": "wing_coach",
    "AML": "wing_coach",
    "AMR": "wing_coach",
    "FC": "attack_coach",
}
POSITION_SKILLS = {
    "GK": {"goalkeeping": 10000},
    "DC": {"defending": 10000},
    "DL": {"defending": 7000, "winger": 3000},
    "DR": {"defending": 7000, "winger": 3000},
    "DM": {"defending": 4000, "playmaking": 6000},
    "MC": {"playmaking": 10000},
    "ML": {"winger": 7000, "playmaking": 3000},
    "MR": {"winger": 7000, "playmaking": 3000},
    "AMC": {"playmaking": 4000, "attacking": 6000},
    "AML": {"winger": 6000, "attacking": 4000},
    "AMR": {"winger": 6000, "attacking": 4000},
    "FC": {"attacking": 10000},
}


def _competition_value(competition: Competition | str) -> str:
    if isinstance(competition, Competition):
        return competition.value
    if type(competition) is str and competition in {item.value for item in Competition}:
        return competition
    raise ValueError("Unknown competition")


def _check_factor(name: str, value: int) -> int:
    if type(value) is not int or not 0 < value < 10000:
        raise ValueError(f"Invalid {name}: expected int in 1..9999 bps")
    return value


def _check_minutes(minutes: int) -> int:
    if type(minutes) is not int or not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise ValueError("Invalid practice minutes: expected int in 0..120")
    return minutes


def _check_seed(match_seed: str) -> str:
    if type(match_seed) is not str or not match_seed:
        raise ValueError("Invalid match seed")
    return match_seed


def _check_policy(policy: str) -> str:
    if policy not in (POLICY, V2_POLICY, V3_POLICY, CURRENT_POLICY):
        raise ValueError("Unknown practice policy")
    return policy


def _check_staff(staff: dict[str, int]) -> dict[str, int]:
    if type(staff) is not dict or any(
        type(key) is not str or type(value) is not int or not 1 <= value <= 20
        for key, value in staff.items()
    ):
        raise ValueError("Invalid practice staff")
    return dict(staff)


def is_official(competition: Competition | str) -> bool:
    """True for league/cup/champions, False for friendlies."""
    return Competition(_competition_value(competition)) in OFFICIAL_COMPETITIONS


def form_up_chance(minutes: int, form: int) -> int:
    """Proposed per-match +1 chance in bps of 10000.

    Minutes scale linearly against a 90-minute base; current form lowers
    the chance so repeated appearances drift upward without fast
    saturation to 8. Zero minutes and form 8 never gain. This is a
    calibration proposal, not an approved balance number.
    """
    _check_minutes(minutes)
    if type(form) is not int or not 1 <= form <= 8:
        raise ValueError("Invalid form")
    if minutes == 0 or form >= 8:
        return 0
    base = 1500 if form <= 3 else 800 if form <= 6 else 300
    return base * minutes // REGULATION_MINUTES


@dataclass(frozen=True)
class FormOutcome:
    form: int
    draw: int
    chance: int
    down_chance: int = 0


def form_chances(minutes: int, form: int, *, policy: str = CURRENT_POLICY) -> tuple[int, int]:
    """V4 uses performance; V3 retains its original table and range to ten.

    One disjoint draw gives -1/0/+1. More minutes give more exposure,
    not larger jumps. Neither edge is absorbing; no appearance means no change.
    """
    _check_minutes(minutes)
    _check_policy(policy)
    if policy == CURRENT_POLICY:
        return performance_chances(form, MatchPerformance(minutes))
    maximum = 10 if policy in (V3_POLICY, CURRENT_POLICY) else 8
    if type(form) is not int or not 1 <= form <= maximum:
        raise ValueError("Invalid form")
    up_table = (1800, 1700, 1500, 1200, 800, 500, 200, 100, 50, 0)
    down_table = (0, 100, 200, 300, 500, 800, 1200, 1800, 2200, 2600)
    up = up_table[form - 1] if form < maximum else 0
    down = down_table[form - 1]
    return up * minutes // 90, down * minutes // 90


def practice_form(
    form: int,
    minutes: int,
    *,
    person_id: UUID,
    match_seed: str,
    policy: str = POLICY,
    performance: MatchPerformance | None = None,
) -> FormOutcome:
    """Seeded form step keyed by policy, match seed and person.

    Same inputs and seed always give the same result; there is no free
    redraw on retry. V4 considers final energy; morale and skill remain separate.
    V1 only rises;
    explicit V2 also permits a one-point fall, with stronger return from extremes.
    """
    _check_policy(policy)
    _check_minutes(minutes)
    if performance is not None and (policy != CURRENT_POLICY or performance.minutes != minutes):
        raise ValueError("Performance belongs to current practice and exact exposure")
    maximum = 10 if policy in (V3_POLICY, CURRENT_POLICY) else 8
    if type(form) is not int or not 1 <= form <= maximum:
        raise ValueError("Invalid form")
    if not isinstance(person_id, UUID):
        raise ValueError("Invalid person id")
    _check_seed(match_seed)
    chance, down = (
        performance_chances(form, performance)
        if performance is not None
        else (
            form_chances(minutes, form, policy=policy)
            if policy in (V2_POLICY, V3_POLICY, CURRENT_POLICY)
            else (form_up_chance(minutes, form), 0)
        )
    )
    draw = seeded_draw(match_seed, person_id, "form", policy=policy)
    if down and chance <= draw < chance + down:
        return FormOutcome(form - 1, draw, chance, down)
    if minutes > 0 and form < maximum and draw < chance:
        return FormOutcome(form + 1, draw, chance, down)
    return FormOutcome(form, draw, chance, down)


@dataclass(frozen=True)
class PracticeTerms:
    """Pin-able terms for a durable Codex snapshot (no DB writes here)."""

    policy: str
    competition: str
    minutes: int
    skill_factor: int
    experience_factor: int
    match_seed: str
    primary_skill: str
    strength: int


@dataclass(frozen=True)
class MatchPracticeResult:
    skills: dict[str, int]
    progress: dict[str, int]
    form: int
    experience: int
    experience_gain: int
    growth: SkillGrowth
    granted: dict[str, int]
    terms: PracticeTerms
    form_draw: int
    form_chance: int
    form_down_chance: int = 0
    position_terms: tuple[dict[str, str | int], ...] = ()
    experience_by_position: dict[str, int] = field(default_factory=dict)
    position_experience_after: dict[str, int] = field(default_factory=dict)
    experience_losses: dict[str, int] = field(default_factory=dict)
    legacy_experience_gain: int = 0


def develop_match_practice(
    player: TrainingPlayer,
    competition: Competition | str,
    minutes: int,
    staff: dict[str, int],
    budget: GrowthBudget,
    *,
    match_seed: str,
    friendly_skill_factor: int | None = None,
    friendly_experience_factor: int | None = None,
    policy: str = POLICY,
) -> MatchPracticeResult:
    """Skill, experience and form practice for one real appearance.

    Official competitions use the ``match-development-v1`` scale verbatim
    (base 240 fractions x age multiplier x specialist strength x
    minutes/90, floored at the end; experience 1000 x age x minutes/90).
    Extra time is total actual minutes up to 120 in the same linear term.
    Friendlies multiply the official skill gain by
    ``friendly_skill_factor`` and the official experience gain by
    ``friendly_experience_factor`` (both proposal ints in 1..9999). Zero
    minutes leave skills, experience, fractions, form and budget values
    unchanged. Both teams use the same rule; the caller supplies each
    player's actual minutes. Inputs are never mutated.
    """
    _check_policy(policy)
    if friendly_skill_factor is None:
        friendly_skill_factor = (
            DEFAULT_FRIENDLY_SKILL_FACTOR if policy == CURRENT_POLICY else LEGACY_FRIENDLY_FACTOR
        )
    if friendly_experience_factor is None:
        friendly_experience_factor = (
            DEFAULT_FRIENDLY_EXPERIENCE_FACTOR
            if policy == CURRENT_POLICY
            else LEGACY_FRIENDLY_FACTOR
        )
    competition_value = _competition_value(competition)
    _check_minutes(minutes)
    _check_seed(match_seed)
    _check_factor("friendly_skill_factor", friendly_skill_factor)
    _check_factor("friendly_experience_factor", friendly_experience_factor)
    if policy == CURRENT_POLICY:
        return develop_position_practice(
            player,
            competition,
            {player.position: minutes} if minutes else {},
            staff,
            budget,
            match_seed=match_seed,
            friendly_skill_factor=friendly_skill_factor,
            friendly_experience_factor=friendly_experience_factor,
        )
    staff_snapshot = _check_staff(staff)
    if not isinstance(player, TrainingPlayer):
        raise ValueError("Invalid practice player")
    if not isinstance(budget, GrowthBudget) or budget.age != player.age:
        raise ValueError("Invalid practice budget")

    official = Competition(competition_value) in OFFICIAL_COMPETITIONS
    skill_factor = 10000 if official else friendly_skill_factor
    experience_factor = 10000 if official else friendly_experience_factor

    roles = POSITION_ROLES if policy == CURRENT_POLICY else LEGACY_POSITION_ROLES
    specialist = staff_snapshot.get(roles[player.position], 0)
    if specialist:
        strength = 5000 + specialist * 500
    else:
        strength = 3000 + staff_snapshot.get("head_coach", 0) * 100
    official_gain = 240 * age_multiplier(player.age) * strength * minutes // (10000**2 * 90)
    requested_gain = official_gain * skill_factor // 10000
    growth = (
        SkillGrowth(
            dict(player.skills),
            dict(player.progress),
            GrowthBudget(budget.age, dict(budget.used)),
            dict.fromkeys(player.skills, 0),
        )
        if minutes == 0
        else grant_growth(
            dict(player.skills),
            dict(player.caps),
            dict(player.progress),
            budget,
            {PRIMARY_SKILLS[player.position]: requested_gain},
            source="match",
        )
    )

    official_experience = 1000 * age_multiplier(player.age) * minutes // (10000 * 90)
    experience_gain = official_experience * experience_factor // 10000
    fractions = dict(growth.progress)
    whole, fractions["experience"] = divmod(fractions["experience"] + experience_gain, 10000)
    growth = SkillGrowth(growth.skills, fractions, growth.budget, growth.granted)

    form_outcome = practice_form(
        player.form, minutes, person_id=player.id, match_seed=match_seed, policy=policy
    )
    return MatchPracticeResult(
        skills=dict(growth.skills),
        progress=dict(fractions),
        form=form_outcome.form,
        experience=player.experience + whole,
        experience_gain=experience_gain,
        growth=growth,
        granted=dict(growth.granted),
        terms=PracticeTerms(
            policy=policy,
            competition=competition_value,
            minutes=minutes,
            skill_factor=skill_factor,
            experience_factor=experience_factor,
            match_seed=match_seed,
            primary_skill=PRIMARY_SKILLS[player.position],
            strength=strength,
        ),
        form_draw=form_outcome.draw,
        form_chance=form_outcome.chance,
        form_down_chance=form_outcome.down_chance,
    )


def develop_position_practice(
    player: TrainingPlayer,
    competition: Competition | str,
    position_minutes: dict[str, int],
    staff: dict[str, int],
    budget: GrowthBudget,
    *,
    match_seed: str,
    friendly_skill_factor: int = DEFAULT_FRIENDLY_SKILL_FACTOR,
    friendly_experience_factor: int = DEFAULT_FRIENDLY_EXPERIENCE_FACTOR,
    performance: MatchPerformance | None = None,
) -> MatchPracticeResult:
    """One atomic appearance grant split by actual position, with one form draw."""
    if type(position_minutes) is not dict or any(
        position not in POSITION_ROLES or type(minutes) is not int or minutes <= 0
        for position, minutes in position_minutes.items()
    ):
        raise ValueError("Invalid positional minutes")
    minutes = _check_minutes(sum(position_minutes.values()))
    competition_value = _competition_value(competition)
    _check_seed(match_seed)
    _check_factor("friendly_skill_factor", friendly_skill_factor)
    _check_factor("friendly_experience_factor", friendly_experience_factor)
    staff = _check_staff(staff)
    if (
        not isinstance(player, TrainingPlayer)
        or not isinstance(budget, GrowthBudget)
        or budget.age != player.age
    ):
        raise ValueError("Invalid positional practice player or budget")
    official = is_official(competition)
    skill_factor = 10000 if official else friendly_skill_factor
    experience_factor = 10000 if official else friendly_experience_factor
    requested = dict.fromkeys(player.skills, 0)
    experience: dict[str, int] = {}
    terms: list[dict[str, str | int]] = []
    for position, played in position_minutes.items():
        role = POSITION_ROLES[position]
        specialist = staff.get(role, 0)
        strength = (
            5000 + specialist * 500 if specialist else 3000 + staff.get("head_coach", 0) * 100
        )
        gain = (
            (240 * age_multiplier(player.age) * strength * played // (10000**2 * 90))
            * skill_factor
            // 10000
        )
        profile = POSITION_SKILLS[position]
        shares = {key: gain * weight // 10000 for key, weight in profile.items()}
        # Keep the whole fractional grant when profile splitting needs rounding.
        shares[next(iter(profile))] += gain - sum(shares.values())
        for key, value in shares.items():
            requested[key] += value
        experience[position] = (
            (1000 * age_multiplier(player.age) * played // (10000 * 90))
            * experience_factor
            // 10000
        )
        terms.append(
            {
                "position": position,
                "minutes": played,
                "role": role,
                "staff_skill": specialist,
                "strength": strength,
                "requested_gain": gain,
            }
        )
    growth = (
        grant_growth(player.skills, player.caps, player.progress, budget, requested, source="match")
        if minutes
        else SkillGrowth(
            dict(player.skills),
            dict(player.progress),
            GrowthBudget(budget.age, dict(budget.used)),
            dict.fromkeys(player.skills, 0),
        )
    )
    fractions = dict(growth.progress)
    experience_gain = sum(experience.values())
    whole, fractions["experience"] = divmod(fractions["experience"] + experience_gain, 10000)
    growth = SkillGrowth(growth.skills, fractions, growth.budget, growth.granted)
    form = practice_form(
        player.form,
        minutes,
        person_id=player.id,
        match_seed=match_seed,
        policy=CURRENT_POLICY,
        performance=performance,
    )
    primary = (
        max(position_minutes, key=position_minutes.__getitem__)
        if position_minutes
        else player.position
    )
    return MatchPracticeResult(
        skills=dict(growth.skills),
        progress=fractions,
        form=form.form,
        experience=player.experience + whole,
        experience_gain=experience_gain,
        growth=growth,
        granted=dict(growth.granted),
        terms=PracticeTerms(
            CURRENT_POLICY,
            competition_value,
            minutes,
            skill_factor,
            experience_factor,
            match_seed,
            max(POSITION_SKILLS[primary], key=POSITION_SKILLS[primary].__getitem__),
            int(terms[0]["strength"]) if terms else 0,
        ),
        form_draw=form.draw,
        form_chance=form.chance,
        form_down_chance=form.down_chance,
        position_terms=tuple(terms),
        experience_by_position=experience,
    )


PERCENT_POLICY = "match-practice-v5"


def develop_percent_practice(
    player: TrainingPlayer,
    competition: Competition | str,
    position_minutes: dict[str, int],
    staff: dict[str, int],
    budget: GrowthBudget,
    *,
    match_seed: str,
    friendly_skill_factor: int = DEFAULT_FRIENDLY_SKILL_FACTOR,
    friendly_experience_factor: int = DEFAULT_FRIENDLY_EXPERIENCE_FACTOR,
    performance: MatchPerformance | None = None,
) -> MatchPracticeResult:
    from dataclasses import replace

    from goaletto.domain.position_experience import percent_practice

    practiced = develop_position_practice(
        player,
        competition,
        position_minutes,
        staff,
        budget,
        match_seed=match_seed,
        friendly_skill_factor=friendly_skill_factor,
        friendly_experience_factor=friendly_experience_factor,
        performance=performance,
    )
    values, grants, losses = percent_practice(
        player.position_experience,
        practiced.experience_by_position,
        player.profile_positions or (player.position,),
        age=player.age,
        decay=True,
        experience_factor=practiced.terms.experience_factor,
    )
    gained = sum(grants.values())
    return replace(
        practiced,
        experience_gain=gained,
        legacy_experience_gain=practiced.experience_gain,
        experience_by_position=grants,
        position_experience_after=values,
        experience_losses=losses,
        terms=replace(practiced.terms, policy=PERCENT_POLICY),
    )
