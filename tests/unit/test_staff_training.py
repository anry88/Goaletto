"""Specialist access, shared growth caps and distinct, gradual staff effects."""

from dataclasses import replace

import pytest

from goaletto.application.development import growth_budget, save_budget
from goaletto.domain.candidates import STAFF_ROLES
from goaletto.domain.development import BUDGET_KEYS, GrowthBudget, grant_growth
from goaletto.domain.staff_effects import mentor_session
from goaletto.domain.training import (
    POSITIONS,
    SPECIALISTS,
    Focus,
    Intensity,
    available_focus,
    train_player,
)
from goaletto.infrastructure.models import PlayerDevelopment
from tests.unit.test_training import player


@pytest.mark.parametrize("focus", list(Focus))
def test_exact_profession_unlocks_every_focus_and_head_quality_never_substitutes(focus):
    for role in STAFF_ROLES:
        assert available_focus(focus, {role: 20}) == (
            focus == Focus.TEAM or SPECIALISTS.get(focus) == role
        )
    assert available_focus(focus, {}) == (focus == Focus.TEAM)
    if focus != Focus.TEAM:
        with pytest.raises(ValueError, match="Specialist"):
            train_player(player(), focus, Intensity.HIGH, seed="locked", trainer_skill=0)
    else:
        assert (
            train_player(
                player(), focus, Intensity.MEDIUM, seed="baseline", trainer_skill=0
            ).gain_bps
            > 0
        )


@pytest.mark.parametrize("position", ["DM", "MC", "AMC"])
def test_central_midfield_has_an_actual_specialist_path(position):
    assert position in POSITIONS[Focus.PLAYMAKING]
    p = player(position=position)
    result = train_player(p, Focus.PLAYMAKING, Intensity.MEDIUM, seed="central", trainer_skill=10)
    assert result.trained and result.progress["playmaking"] > 0
    assert result.progress["defending"] == result.progress["attacking"] == 0


def test_general_primary_skill_quality_age_and_physical_split():
    for age in (18, 21, 26, 30):
        for skill in (1, 5, 9, 13, 17, 20):
            p = player(age)
            result = train_player(
                p,
                Focus.TEAM,
                Intensity.MEDIUM,
                seed="general",
                trainer_skill=skill,
                head_skill=skill,
            )
            assert 0 < result.gain_bps < 200
            assert result.progress["playmaking"] == result.gain_bps
            assert all(result.progress[k] == 0 for k in p.skills if k != "playmaking")
    specialist_with_weak_head = train_player(
        player(18), Focus.PHYSICAL, Intensity.HIGH, seed="same", trainer_skill=10, head_skill=1
    )
    specialist_with_elite_head = train_player(
        player(18), Focus.PHYSICAL, Intensity.HIGH, seed="same", trainer_skill=10, head_skill=20
    )
    assert specialist_with_weak_head.gain_bps == specialist_with_elite_head.gain_bps
    assert specialist_with_weak_head.leadership_gain < specialist_with_elite_head.leadership_gain
    weak = train_player(player(18), Focus.PHYSICAL, Intensity.HIGH, seed="same", trainer_skill=1)
    elite = train_player(player(18), Focus.PHYSICAL, Intensity.HIGH, seed="same", trainer_skill=20)
    veteran = train_player(
        player(30), Focus.PHYSICAL, Intensity.HIGH, seed="same", trainer_skill=20
    )
    assert elite.gain_bps > weak.gain_bps and elite.gain_bps > veteran.gain_bps
    assert elite.progress["stamina"] + elite.progress["pace"] == elite.gain_bps
    assert abs(elite.progress["stamina"] - elite.progress["pace"]) <= 1


def test_shared_cap_all_sources_and_individual_caps_cannot_be_farmed():
    p = player(18)
    budget = GrowthBudget(18)
    result = grant_growth(
        p.skills, p.caps, p.progress, budget, {"stamina": 15000, "pace": 15000}, source="training"
    )
    assert sum(result.granted.values()) == 20000
    assert result.budget.used["stamina"] == 15000
    result = grant_growth(
        result.skills, p.caps, result.progress, result.budget, {"playmaking": 20000}, source="match"
    )
    assert sum(result.granted.values()) == 10000
    assert sum(result.budget.used[k] for k in p.skills) == 30000
    again = grant_growth(
        result.skills,
        p.caps,
        result.progress,
        result.budget,
        {k: 10000 for k in p.skills},
        source="training",
    )
    assert again.skills == result.skills and sum(again.granted.values()) == 0
    assert budget.used == dict.fromkeys(BUDGET_KEYS, 0)  # Pure inputs are unchanged.


def test_budget_follows_age_and_preserves_existing_potential_and_fractions():
    p = player(21)
    dev = PlayerDevelopment(
        growth_policy="development-budget-v1",
        growth_age=21,
        season_gains={**dict.fromkeys(BUDGET_KEYS, 0), "playmaking": 1000, "training": 1000},
        caps=p.caps,
        progress={**p.progress, "playmaking": 3333},
    )
    original = growth_budget(dev, 21)
    save_budget(dev, original)
    assert growth_budget(dev, 21).used["training"] == 1000
    assert growth_budget(dev, 22).used["training"] == 0
    assert dev.progress["playmaking"] == 3333 and dev.caps == p.caps
    with pytest.raises(ValueError, match="backwards"):
        growth_budget(dev, 20)
    with pytest.raises(ValueError):
        GrowthBudget(21, {**original.used, "match": 1})


def test_leadership_is_separate_gradual_bounded_and_not_experience_or_form():
    p = player(18)
    budget, fraction, leadership, morale = GrowthBudget(18), 0, 10, 50
    for day in range(80):
        result = mentor_session(
            p.id, morale, leadership, fraction, budget, head_skill=20, seed=str(day)
        )
        assert result == mentor_session(
            p.id, morale, leadership, fraction, budget, head_skill=20, seed=str(day)
        )
        morale, leadership, fraction, budget = (
            result.morale,
            result.leadership,
            result.leadership_progress,
            result.budget,
        )
    assert 50 < morale <= 70 and leadership == 10 and fraction == 5000
    assert (
        budget.used["leadership"] == 5000 and budget.used["training"] == budget.used["match"] == 0
    )
    no_coach = mentor_session(p.id, 80, 10, 0, GrowthBudget(18), head_skill=0, seed="baseline")
    assert no_coach.morale == 80 and no_coach.leadership_progress == 0
    near_point = mentor_session(
        p.id, 50, 10, 9990, GrowthBudget(18), head_skill=20, seed="threshold"
    )
    assert (near_point.leadership, near_point.leadership_progress) == (11, 90)
    at_cap = mentor_session(p.id, 100, 20, 9999, GrowthBudget(18), head_skill=20, seed="cap")
    assert at_cap.leadership == 20 and at_cap.leadership_progress == 0 and at_cap.morale == 100
    skipped = train_player(
        replace(p, injured=True),
        Focus.TEAM,
        Intensity.HIGH,
        seed="skip",
        trainer_skill=20,
        head_skill=20,
    )
    assert skipped.morale == p.morale and skipped.leadership_gain == 0
    assert skipped.form == p.form and skipped.experience == p.experience


@pytest.mark.parametrize("age", [18, 21, 26, 30])
@pytest.mark.parametrize("skill", [1, 5, 9, 13, 17, 20])
def test_eighty_day_extreme_never_exceeds_seasonal_skill_and_leadership_caps(age, skill):
    p, budget = player(age), GrowthBudget(age)
    for day in range(80):
        result = train_player(
            p,
            Focus.PHYSICAL if day % 2 else Focus.PLAYMAKING,
            Intensity.HIGH,
            seed=f"stress:{day}",
            trainer_skill=skill,
            head_skill=skill,
            budget=budget,
        )
        p = replace(
            p,
            skills=result.skills,
            progress=result.progress,
            form=result.form,
            experience=result.experience,
            morale=result.morale,
            leadership=result.leadership,
            leadership_progress=result.leadership_progress,
        )
        budget = result.budget
        if day % 4 == 3:
            match = grant_growth(
                p.skills, p.caps, p.progress, budget, {"playmaking": 2000}, source="match"
            )
            p, budget = replace(p, skills=match.skills, progress=match.progress), match.budget
    assert budget.used["training"] <= 20000 and budget.used["match"] <= 10000
    assert sum(budget.used[k] for k in p.skills) <= 30000
    assert all(budget.used[k] <= 15000 for k in p.skills)
    assert p.leadership_progress <= 5000 and p.leadership == 10 and p.morale <= 50 + skill
