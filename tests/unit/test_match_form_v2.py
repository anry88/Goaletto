"""Replay-safe form dynamics and compatibility of the two practice versions."""

from dataclasses import replace
from uuid import UUID

import pytest

from goaletto.domain.development import GrowthBudget
from goaletto.domain.match_practice import (
    CURRENT_POLICY,
    POLICY,
    Competition,
    develop_match_practice,
    form_chances,
    practice_form,
)
from tests.unit.test_match_practice import make_player


@pytest.mark.parametrize("form", range(1, 9))
@pytest.mark.parametrize("minutes", [0, 1, 30, 90, 120])
def test_disjoint_bounded_chances_and_no_minute_no_effect(form, minutes, monkeypatch):
    up, down = form_chances(minutes, form)
    assert 0 <= up + down < 10000
    if minutes == 0:
        assert up == down == 0
    if form == 1:
        assert down == 0
    if form == 8:
        assert up == 0
    for draw in {0, max(0, up - 1), up, max(0, up + down - 1), up + down, 9999}:
        monkeypatch.setattr(
            "goaletto.domain.match_practice.seeded_draw", lambda *a, value=draw, **k: value
        )
        outcome = practice_form(
            form, minutes, person_id=UUID(int=1), match_seed="one", policy=CURRENT_POLICY
        )
        expected = form + (draw < up) - (up <= draw < up + down)
        assert outcome.form == expected
        assert 1 <= outcome.form <= 8
        assert outcome.chance == up and outcome.down_chance == down


def test_form_eight_can_fall_and_form_one_can_rise():
    top = [
        practice_form(
            8, 90, person_id=UUID(int=i), match_seed="extreme", policy=CURRENT_POLICY
        ).form
        for i in range(1000)
    ]
    low = [
        practice_form(
            1, 90, person_id=UUID(int=i), match_seed="extreme", policy=CURRENT_POLICY
        ).form
        for i in range(1000)
    ]
    assert 100 < top.count(7) < 260
    assert 100 < low.count(2) < 260
    assert set(top) == {7, 8} and set(low) == {1, 2}


@pytest.mark.parametrize("competition", tuple(Competition))
def test_v2_preserves_official_and_friendly_growth_scale_and_inputs(competition):
    player = make_player()
    budget = GrowthBudget(22)
    old = develop_match_practice(
        player, competition, 120, {"attack_coach": 10}, budget, match_seed="same"
    )
    current = develop_match_practice(
        player,
        competition,
        120,
        {"attack_coach": 10},
        budget,
        match_seed="same",
        policy=CURRENT_POLICY,
    )
    assert current.growth == old.growth
    assert current.experience == old.experience
    assert current.terms.policy == CURRENT_POLICY and old.terms.policy == POLICY
    assert current == develop_match_practice(
        player,
        competition,
        120,
        {"attack_coach": 10},
        budget,
        match_seed="same",
        policy=CURRENT_POLICY,
    )
    unused = develop_match_practice(
        replace(player, form=8),
        competition,
        0,
        {},
        budget,
        match_seed="unused",
        policy=CURRENT_POLICY,
    )
    assert unused.form == 8 and unused.growth.budget == budget
    assert unused.progress == player.progress and unused.skills == player.skills


@pytest.mark.parametrize("form", [True, 0, 9, 5.0])
def test_invalid_form_rejected(form):
    with pytest.raises(ValueError):
        form_chances(90, form)
