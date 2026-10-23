"""Ten-point form is reachable, bounded and does not change frozen V2 outcomes."""

from dataclasses import replace
from uuid import UUID

import pytest

from goaletto.domain.development import GrowthBudget
from goaletto.domain.match_practice import CURRENT_POLICY, V2_POLICY, form_chances, practice_form
from goaletto.domain.training import Focus, Intensity, train_player
from tests.unit.test_match_practice import make_player


@pytest.mark.parametrize("form", range(1, 11))
@pytest.mark.parametrize("minutes", (0, 30, 90, 120))
def test_new_form_edges_and_disjoint_draws(form, minutes, monkeypatch):
    up, down = form_chances(minutes, form)
    if not minutes:
        assert up == down == 0
    if form == 10:
        assert up == 0 and (down > 0 or not minutes)
    for draw in {0, up, up + down, 9999}:
        monkeypatch.setattr(
            "goaletto.domain.match_practice.seeded_draw", lambda *a, value=draw, **k: value
        )
        result = practice_form(
            form, minutes, person_id=UUID(int=1), match_seed="ten", policy=CURRENT_POLICY
        )
        assert result.form == form + (draw < up) - (up <= draw < up + down)
        assert 1 <= result.form <= 10


def test_eight_and_nine_can_rise_but_old_eight_cannot(monkeypatch):
    monkeypatch.setattr("goaletto.domain.match_practice.seeded_draw", lambda *a, **k: 0)
    for form in (8, 9):
        assert (
            practice_form(
                form, 90, person_id=UUID(int=1), match_seed="ten", policy=CURRENT_POLICY
            ).form
            == form + 1
        )
    assert practice_form(8, 90, person_id=UUID(int=1), match_seed="ten", policy=V2_POLICY).form == 7
    assert form_chances(90, 8, policy=V2_POLICY) == (0, 1800)


@pytest.mark.parametrize("form,expected", ((8, 9), (9, 10), (10, 10)))
def test_training_reaches_ten_without_lowering_high_form(form, expected, monkeypatch):
    monkeypatch.setattr("goaletto.domain.training.seeded_draw", lambda *a, **k: 0)
    player = replace(make_player(), form=form)
    result = train_player(
        player,
        Focus.TEAM,
        Intensity.LOW,
        trainer_skill=10,
        seed="ten",
        budget=GrowthBudget(player.age),
    )
    assert result.form == expected


@pytest.mark.parametrize("form", (0, 11, True, 10.0))
def test_invalid_form_rejected(form):
    with pytest.raises(ValueError):
        form_chances(90, form)
