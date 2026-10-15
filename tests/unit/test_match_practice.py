"""AND-130 match practice: every real game grants form and development."""

from dataclasses import replace
from uuid import UUID

import pytest

from goaletto.domain.development import GrowthBudget
from goaletto.domain.match_development import POSITION_ROLES, develop_appearance
from goaletto.domain.match_practice import (
    DEFAULT_FRIENDLY_EXPERIENCE_FACTOR,
    DEFAULT_FRIENDLY_SKILL_FACTOR,
    POLICY,
    Competition,
    develop_match_practice,
    form_up_chance,
    is_official,
    practice_form,
)
from goaletto.domain.training import PROGRESS_KEYS, SKILLS, TrainingPlayer


def make_player(age=22, position="MC", form=6, skill=10, cap=15):
    return TrainingPlayer(
        UUID(int=1),
        age,
        position,
        dict.fromkeys(SKILLS, skill),
        dict.fromkeys(SKILLS, cap),
        dict.fromkeys(PROGRESS_KEYS, 0),
        form,
        0,
    )


def role_for(position):
    return POSITION_ROLES[position]


@pytest.mark.parametrize("minutes", [0, 1, 30, 45, 90, 105, 120])
def test_minutes_ladder_official_scales_without_double_counting(minutes):
    player = make_player(22)
    staff = {role_for("MC"): 10}
    result = develop_match_practice(
        player, "league", minutes, staff, GrowthBudget(22), match_seed="m1"
    )
    assert result.terms.minutes == minutes
    assert result.terms.skill_factor == 10000
    if minutes == 0:
        assert result.granted == dict.fromkeys(SKILLS, 0)
        assert result.experience_gain == 0
        assert result.form == player.form
        assert result.skills == player.skills
        assert result.progress == player.progress
        assert result.experience == player.experience
    else:
        assert result.experience_gain > 0 or minutes < 90  # tiny gains may floor
    full = develop_match_practice(player, "league", 90, staff, GrowthBudget(22), match_seed="m1")
    extra = develop_match_practice(player, "league", 120, staff, GrowthBudget(22), match_seed="m1")
    assert sum(extra.granted.values()) >= sum(full.granted.values())
    assert sum(extra.granted.values()) < 2 * sum(full.granted.values())
    assert extra.experience_gain >= full.experience_gain


@pytest.mark.parametrize("age", [18, 21, 26, 30, 34])
def test_younger_gains_more_than_older(age):
    young = develop_match_practice(
        make_player(18),
        "league",
        90,
        {"attack_coach": 10},
        GrowthBudget(18),
        match_seed="age",
    )
    old = develop_match_practice(
        make_player(age),
        "league",
        90,
        {"attack_coach": 10},
        GrowthBudget(age),
        match_seed="age",
    )
    if age > 18:
        assert sum(old.granted.values()) < sum(young.granted.values())
        assert old.experience_gain < young.experience_gain


@pytest.mark.parametrize("position", tuple(POSITION_ROLES))
def test_only_primary_skill_granted_and_staff_roles_match(position):
    player = make_player(22, position)
    role = role_for(position)
    result = develop_match_practice(
        player, "league", 90, {role: 10}, GrowthBudget(22), match_seed="pos"
    )
    granted = {k: v for k, v in result.granted.items() if v}
    assert set(granted) <= {"goalkeeping", "defending", "playmaking", "winger", "attacking"}
    assert len(granted) <= 1


def test_specialist_vacancy_and_irrelevant_staff():
    player = make_player(22, "MC")
    full = develop_match_practice(
        player, "league", 90, {"attack_coach": 10}, GrowthBudget(22), match_seed="s"
    )
    weak = develop_match_practice(
        player, "league", 90, {"attack_coach": 1}, GrowthBudget(22), match_seed="s"
    )
    vacant = develop_match_practice(player, "league", 90, {}, GrowthBudget(22), match_seed="s")
    vacant_head = develop_match_practice(
        player, "league", 90, {"head_coach": 10}, GrowthBudget(22), match_seed="s"
    )
    irrelevant = develop_match_practice(
        player,
        "league",
        90,
        {"physiotherapist": 20, "fitness_coach": 20, "scout": 20},
        GrowthBudget(22),
        match_seed="s",
    )
    with_staff = develop_match_practice(
        player,
        "league",
        90,
        {"attack_coach": 10, "physiotherapist": 20, "fitness_coach": 20},
        GrowthBudget(22),
        match_seed="s",
    )
    assert sum(weak.granted.values()) < sum(full.granted.values())
    assert sum(vacant.granted.values()) <= sum(vacant_head.granted.values())
    assert sum(vacant_head.granted.values()) < sum(full.granted.values())
    assert irrelevant.granted == vacant.granted
    # Irrelevant staff never adds a second multiplier.
    assert with_staff.granted == full.granted
    assert with_staff.experience_gain == full.experience_gain


def test_official_competitions_share_one_scale():
    player = make_player(26, "FC")
    staff = {"attack_coach": 12}
    budget = GrowthBudget(26)
    league = develop_match_practice(player, "league", 90, staff, budget, match_seed="off")
    cup = develop_match_practice(player, "cup", 90, staff, budget, match_seed="off")
    champions = develop_match_practice(
        player, Competition.CHAMPIONS, 90, staff, budget, match_seed="off"
    )
    assert league.granted == cup.granted == champions.granted
    assert league.experience_gain == cup.experience_gain == champions.experience_gain
    assert league.form == cup.form == champions.form
    assert is_official("league") and is_official("cup") and is_official(Competition.CHAMPIONS)
    assert not is_official("friendly") and not is_official(Competition.FRIENDLY)


def test_friendly_below_official_with_free_budget():
    player = make_player(22)
    staff = {"attack_coach": 10}
    official = develop_match_practice(player, "league", 90, staff, GrowthBudget(22), match_seed="f")
    friendly = develop_match_practice(
        player, "friendly", 90, staff, GrowthBudget(22), match_seed="f"
    )
    assert sum(friendly.granted.values()) > 0
    assert sum(friendly.granted.values()) < sum(official.granted.values())
    assert 0 < friendly.experience_gain < official.experience_gain
    assert friendly.terms.skill_factor == DEFAULT_FRIENDLY_SKILL_FACTOR
    assert friendly.terms.experience_factor == DEFAULT_FRIENDLY_EXPERIENCE_FACTOR
    low = develop_match_practice(
        player,
        "friendly",
        90,
        staff,
        GrowthBudget(22),
        match_seed="f",
        friendly_skill_factor=2500,
        friendly_experience_factor=2500,
    )
    high = develop_match_practice(
        player,
        "friendly",
        90,
        staff,
        GrowthBudget(22),
        match_seed="f",
        friendly_skill_factor=7500,
        friendly_experience_factor=7500,
    )
    assert sum(low.granted.values()) < sum(friendly.granted.values()) < sum(high.granted.values())
    assert sum(high.granted.values()) < sum(official.granted.values())


def test_skill_and_experience_factors_are_independent_decisions():
    player = make_player(22)
    staff = {"attack_coach": 10}
    skill_low = develop_match_practice(
        player,
        "friendly",
        90,
        staff,
        GrowthBudget(22),
        match_seed="ind",
        friendly_skill_factor=2500,
        friendly_experience_factor=7500,
    )
    skill_high = develop_match_practice(
        player,
        "friendly",
        90,
        staff,
        GrowthBudget(22),
        match_seed="ind",
        friendly_skill_factor=7500,
        friendly_experience_factor=2500,
    )
    assert sum(skill_low.granted.values()) < sum(skill_high.granted.values())
    assert skill_low.experience_gain > skill_high.experience_gain


def test_official_ninety_matches_v1_scale():
    for age, position in [(18, "MC"), (22, "FC"), (30, "GK"), (26, "DC")]:
        player = make_player(age, position)
        staff = {role_for(position): 10}
        old = develop_appearance(player, 90, staff, GrowthBudget(age))
        new = develop_match_practice(
            player, "league", 90, staff, GrowthBudget(age), match_seed="v1"
        )
        assert new.granted == old.growth.granted
        assert new.skills == old.growth.skills
        assert new.experience_gain == old.experience_gain
        assert new.experience == old.experience
        assert new.growth.budget.used == old.growth.budget.used


def test_capped_potential_gives_zero_skill_but_keeps_experience():
    player = make_player(21)
    player = replace(
        player,
        caps=dict(player.skills),
        progress={**player.progress, "experience": 9999},
    )
    result = develop_match_practice(
        player, "league", 90, {"attack_coach": 20}, GrowthBudget(21), match_seed="cap"
    )
    assert not any(result.granted.values())
    assert result.skills == player.skills
    assert result.experience == player.experience + 1
    friendly = develop_match_practice(
        player, "friendly", 90, {"attack_coach": 20}, GrowthBudget(21), match_seed="cap"
    )
    assert not any(friendly.granted.values())
    # Zero skill grant at caps/potential is not an error.


def test_budget_fractions_carry_over_and_transfer_does_not_reset():
    player = make_player(22)
    staff = {"attack_coach": 10}
    first = develop_match_practice(player, "league", 90, staff, GrowthBudget(22), match_seed="b1")
    carried = develop_match_practice(
        replace(player, skills=first.skills, progress=first.progress),
        "cup",
        90,
        staff,
        first.growth.budget,
        match_seed="b2",
    )
    assert carried.growth.budget.used["match"] == (
        first.growth.budget.used["match"] + sum(carried.granted.values())
    )
    assert (carried.skills["playmaking"] * 10000 + carried.progress["playmaking"]) == (
        first.skills["playmaking"] * 10000
        + first.progress["playmaking"]
        + carried.granted["playmaking"]
    )
    # Same budget object follows the person across clubs/owners: no new budget.


def test_match_source_cap_blocks_further_match_gains():
    player = make_player(22)
    staff = {"attack_coach": 20}
    used = dict(GrowthBudget(22).used)
    used["playmaking"] = 10000
    used["match"] = 10000
    used["training"] = 0
    # Build a budget at the match source limit with consistent totals.
    capped = GrowthBudget(22, used)
    result = develop_match_practice(player, "league", 90, staff, capped, match_seed="lim")
    assert not any(result.granted.values())


def test_form_stays_in_range_reproducible_and_not_guaranteed():
    for form in (1, 4, 6, 7, 8):
        outcome = practice_form(form, 90, person_id=UUID(int=7), match_seed="seed")
        assert 1 <= outcome.form <= 8
        assert outcome.form in (form, min(8, form + 1))
        again = practice_form(form, 90, person_id=UUID(int=7), match_seed="seed")
        assert again == outcome
    assert practice_form(8, 120, person_id=UUID(int=7), match_seed="seed").form == 8
    assert practice_form(6, 0, person_id=UUID(int=7), match_seed="seed").form == 6
    assert form_up_chance(0, 4) == 0
    assert form_up_chance(90, 8) == 0
    assert form_up_chance(90, 1) > form_up_chance(90, 6) > form_up_chance(90, 7) > 0
    assert form_up_chance(30, 6) < form_up_chance(90, 6) < form_up_chance(120, 6)
    ups = sum(
        practice_form(6, 90, person_id=UUID(int=i), match_seed="dist").form == 7 for i in range(500)
    )
    assert 0 < ups < 500  # probabilistic, never guaranteed nor impossible
    assert 10 < ups < 120  # roughly 8% at form 6 over 90 minutes


def test_inputs_are_not_mutated_and_terms_are_pinnable():
    player = make_player(22)
    staff = {"attack_coach": 10}
    skills_before = dict(player.skills)
    progress_before = dict(player.progress)
    budget = GrowthBudget(22)
    used_before = dict(budget.used)
    result = develop_match_practice(player, Competition.CUP, 90, staff, budget, match_seed="pin")
    assert player.skills == skills_before and player.progress == progress_before
    assert budget.used == used_before
    assert staff == {"attack_coach": 10}
    assert result.terms.policy == POLICY
    assert result.terms.competition == "cup"
    assert result.terms.match_seed == "pin"
    assert result.terms.primary_skill == "playmaking"
    assert result.terms.strength == 5000 + 10 * 500


@pytest.mark.parametrize("bad_minutes", [True, False, 1.0, 90.0, -1, 121, "90", None])
def test_invalid_minutes_rejected(bad_minutes):
    with pytest.raises(ValueError):
        develop_match_practice(
            make_player(), "league", bad_minutes, {}, GrowthBudget(22), match_seed="x"
        )
    with pytest.raises(ValueError):
        practice_form(6, bad_minutes, person_id=UUID(int=1), match_seed="x")


@pytest.mark.parametrize("bad_competition", ["tournament", "", "League", 1, None, True, b"league"])
def test_unknown_competition_rejected(bad_competition):
    with pytest.raises(ValueError):
        develop_match_practice(
            make_player(), bad_competition, 90, {}, GrowthBudget(22), match_seed="x"
        )
    with pytest.raises(ValueError):
        is_official(bad_competition)


@pytest.mark.parametrize("bad_factor", [0, 10000, -5, 20000, True, False, 50.0, "5000", None])
def test_invalid_factors_rejected(bad_factor):
    with pytest.raises(ValueError):
        develop_match_practice(
            make_player(),
            "friendly",
            90,
            {},
            GrowthBudget(22),
            match_seed="x",
            friendly_skill_factor=bad_factor,
        )
    with pytest.raises(ValueError):
        develop_match_practice(
            make_player(),
            "friendly",
            90,
            {},
            GrowthBudget(22),
            match_seed="x",
            friendly_experience_factor=bad_factor,
        )


def test_unknown_policy_and_bad_seed_staff_rejected():
    with pytest.raises(ValueError):
        develop_match_practice(
            make_player(),
            "league",
            90,
            {},
            GrowthBudget(22),
            match_seed="x",
            policy="match-practice-v99",
        )
    with pytest.raises(ValueError):
        practice_form(6, 90, person_id=UUID(int=1), match_seed="x", policy="nope")
    for bad_seed in ("", None, 123, b"s"):
        with pytest.raises(ValueError):
            develop_match_practice(
                make_player(), "league", 90, {}, GrowthBudget(22), match_seed=bad_seed
            )
    with pytest.raises(ValueError):
        develop_match_practice(
            make_player(),
            "league",
            90,
            {"attack_coach": 0},
            GrowthBudget(22),
            match_seed="x",
        )
    with pytest.raises(ValueError):
        develop_match_practice(
            make_player(),
            "league",
            90,
            {"attack_coach": 21},
            GrowthBudget(22),
            match_seed="x",
        )
    with pytest.raises(ValueError):
        develop_match_practice(make_player(22), "league", 90, {}, GrowthBudget(30), match_seed="x")


@pytest.mark.parametrize("competition", tuple(Competition))
def test_unused_player_preserves_capped_fractional_progress(competition):
    player = make_player(skill=10, cap=10)
    player = replace(player, progress={**player.progress, "playmaking": 1234, "experience": 9999})
    budget = GrowthBudget(player.age)
    result = develop_match_practice(player, competition, 0, {}, budget, match_seed="unused")
    assert result.progress == player.progress
    assert result.skills == player.skills and result.form == player.form
    assert result.experience == player.experience and result.experience_gain == 0
    assert result.growth.budget.used == budget.used
    assert not any(result.granted.values())
