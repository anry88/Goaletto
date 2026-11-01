from dataclasses import replace
from uuid import UUID

import pytest

from goaletto.domain.development import BUDGET_KEYS, GrowthBudget
from goaletto.domain.training import (
    LEGACY_SESSION_POLICY,
    POSITIONS,
    Focus,
    Intensity,
    train_player,
)
from tests.unit.test_training import player


@pytest.mark.parametrize("age", [17, 22, 27, 32, 38])
@pytest.mark.parametrize(
    "position", ["GK", "DC", "DL", "DR", "DM", "MC", "AMC", "ML", "MR", "AML", "AMR", "FC"]
)
@pytest.mark.parametrize("focus", list(POSITIONS) + [Focus.SET_PIECES])
def test_every_position_attends_and_profile_outcomes_remain_identical(age, position, focus):
    p = player(age, position)
    executor = p.id if position == "FC" else UUID(int=99)
    kwargs = dict(
        seed="pinned-session", trainer_skill=10, head_skill=10, physio_skill=8, executor_id=executor
    )
    old = train_player(p, focus, Intensity.MEDIUM, policy=LEGACY_SESSION_POLICY, **kwargs)
    current = train_player(p, focus, Intensity.MEDIUM, **kwargs)
    profile = (
        position in POSITIONS.get(focus, ()) if focus != Focus.SET_PIECES else p.id == executor
    )
    assert current.trained
    if profile:
        assert current.skills == old.skills
        assert current.gain_bps == old.gain_bps
        assert current.form == old.form and current.injured == old.injured
        assert current.budget == old.budget
        assert current.leadership_gain == old.leadership_gain
    else:
        assert not old.trained
        main = replace(p, position=next(iter(POSITIONS[focus]))) if focus != Focus.SET_PIECES else p
        main_kwargs = {**kwargs, "executor_id": main.id}
        primary = train_player(main, focus, Intensity.MEDIUM, **main_kwargs)
        assert current.gain_bps == primary.gain_bps // 10 > 0
        assert current.progress[focus.value] == current.gain_bps
        assert all(
            current.progress[key] == p.progress[key] for key in p.skills if key != focus.value
        )
        assert current.experience == old.experience
        assert current.progress["experience"] > old.progress["experience"]
        assert current.leadership_gain > 0


def test_nonprofile_growth_is_age_dependent_and_clamped_by_individual_budget_and_caps():
    young = train_player(
        player(17, "FC"), Focus.DEFENDING, Intensity.HIGH, seed="age", trainer_skill=20
    )
    veteran = train_player(
        player(38, "FC"), Focus.DEFENDING, Intensity.HIGH, seed="age", trainer_skill=20
    )
    assert young.gain_bps > veteran.gain_bps > 0
    p = player(22, "FC")
    capped = replace(p, caps={**p.caps, "defending": 10})
    assert (
        train_player(
            capped, Focus.DEFENDING, Intensity.MEDIUM, seed="cap", trainer_skill=10
        ).gain_bps
        == 0
    )
    budget = GrowthBudget(
        22,
        {**dict.fromkeys(BUDGET_KEYS, 0), "attacking": 14999, "defending": 5000, "training": 19999},
    )
    last = train_player(
        p, Focus.DEFENDING, Intensity.MEDIUM, seed="budget", trainer_skill=10, budget=budget
    )
    assert last.gain_bps == 1 and last.budget.used["training"] == 20000
    injured = replace(p, injured=True)
    skipped = train_player(
        injured, Focus.DEFENDING, Intensity.HIGH, seed="injury", trainer_skill=10, head_skill=20
    )
    assert (
        not skipped.trained
        and skipped.progress == injured.progress
        and skipped.leadership_gain == 0
    )


def test_unknown_policy_and_missing_selected_taker_are_rejected():
    with pytest.raises(ValueError, match="policy"):
        train_player(
            player(), Focus.TEAM, Intensity.LOW, seed="x", trainer_skill=1, policy="unknown"
        )
    with pytest.raises(ValueError, match="taker"):
        train_player(player(), Focus.SET_PIECES, Intensity.LOW, seed="x", trainer_skill=1)
