from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from goaletto.application.training import TrainingMember, TrainingScreen, week_start
from goaletto.bot.training_views import choice_callback, parse_choice, training_view
from goaletto.domain.training import (
    PROGRESS_KEYS,
    SKILLS,
    Focus,
    Intensity,
    TrainingPlayer,
    age_multiplier,
    coach_bonus,
    initial_caps,
    train_player,
)


def player(age=22, position="MC"):
    skills = dict.fromkeys(SKILLS, 10)
    return TrainingPlayer(
        UUID(int=1),
        age,
        position,
        skills,
        dict.fromkeys(SKILLS, 15),
        dict.fromkeys(PROGRESS_KEYS, 0),
        6,
        0,
    )


def test_progress_accumulates_without_float_drift_and_stops_at_potential():
    p = player()
    for _ in range(3):
        result = train_player(p, Focus.PLAYMAKING, Intensity.MEDIUM, seed="week")
        p = replace(p, skills=result.skills, progress=result.progress, form=result.form)
    assert p.skills["playmaking"] == 11 and p.progress["playmaking"] == 4400
    capped = replace(
        p, skills={**p.skills, "playmaking": 15}, progress={**p.progress, "playmaking": 9900}
    )
    result = train_player(capped, Focus.PLAYMAKING, Intensity.HIGH, seed="week")
    assert result.skills["playmaking"] == 15 and result.progress["playmaking"] == 0
    assert p.skills["defending"] == 10 and p.progress["defending"] == 0


def test_age_anchors_coaches_and_fractional_physical_gain():
    assert [age_multiplier(a) for a in (17, 22, 27, 32, 38)] == [15000, 12000, 8000, 3000, 3000]
    assert coach_bonus(Focus.ATTACKING, {"head_coach": 20, "attack_coach": 20}) == 5000
    assert coach_bonus(Focus.DEFENDING, {"head_coach": 20, "attack_coach": 20}) == 2000
    result = train_player(player(17), Focus.PHYSICAL, Intensity.HIGH, seed="first", bonus_bps=5000)
    assert result.skills["stamina"] == 11 and result.skills["pace"] == 11
    assert result.progress["pace"] == result.progress["stamina"] == 1700
    result = train_player(player(32), Focus.TEAM, Intensity.LOW, seed="first")
    assert result.progress["experience"] == 720


def test_eligible_positions_executor_and_injury_skip():
    p = player(position="GK")
    assert not train_player(p, Focus.ATTACKING, Intensity.MEDIUM, seed="week").trained
    assert train_player(p, Focus.GOALKEEPING, Intensity.MEDIUM, seed="week").trained
    assert not train_player(
        p, Focus.SET_PIECES, Intensity.MEDIUM, seed="week", executor_id=uuid4()
    ).trained
    assert train_player(
        p, Focus.SET_PIECES, Intensity.MEDIUM, seed="week", executor_id=p.id
    ).trained
    result = train_player(replace(p, injured=True), Focus.PHYSICAL, Intensity.HIGH, seed="week")
    assert not result.trained and result.skills == p.skills and result.progress == p.progress
    assert result.form == p.form and not result.injured


def test_seeded_risk_is_repeatable_and_physio_can_only_reduce_it():
    p = player()
    injury_seeds = [
        str(i)
        for i in range(1000)
        if train_player(p, Focus.PHYSICAL, Intensity.HIGH, seed=str(i)).injured
    ]
    assert 1 <= len(injury_seeds) <= 25
    for seed in injury_seeds:
        first = train_player(p, Focus.PHYSICAL, Intensity.HIGH, seed=seed)
        assert first == train_player(p, Focus.PHYSICAL, Intensity.HIGH, seed=seed)
        assert not train_player(p, Focus.PHYSICAL, Intensity.MEDIUM, seed=seed).injured
    for i in range(100):
        normal = train_player(p, Focus.PHYSICAL, Intensity.HIGH, seed=str(i))
        reduced = train_player(p, Focus.PHYSICAL, Intensity.HIGH, seed=str(i), physio_skill=20)
        assert not reduced.injured or normal.injured
    caps = initial_caps(p.id, p.skills)
    assert caps == initial_caps(p.id, p.skills) and all(12 <= v <= 20 for v in caps.values())


def test_week_callbacks_are_stable_and_fit_with_both_full_uuids():
    at = datetime(2026, 10, 18, 23, 59, tzinfo=UTC)
    week = week_start(at)
    assert week == datetime(2026, 10, 12, tzinfo=UTC)
    screen = TrainingScreen(
        "<Клуб>",
        uuid4(),
        week,
        week + timedelta(days=7),
        None,
        None,
        (TrainingMember(uuid4(), "<Игрок>", "MC"),),
    )
    for f in Focus:
        for i in Intensity:
            executor = screen.members[0].id if f == Focus.SET_PIECES else None
            data = choice_callback("s", screen, f, i, executor)
            assert len(data.encode()) <= 64
            assert parse_choice(data) == ("s", screen.ownership_id, week, f, i, executor)
    text, kb = training_view(screen, focus=Focus.SET_PIECES, intensity=Intensity.HIGH)
    assert "&lt;Клуб&gt;" in text and "риск травмы" in text
    assert not any(b.text == "✅ Сохранить тренировку" for row in kb.inline_keyboard for b in row)
    for bad in ("tr:s:9:1:id:20261012:-", "tr:s:0:1:id:20260230:-", "tr:s:00:1:id:20261012:-"):
        with pytest.raises(ValueError):
            parse_choice(bad)


def test_training_report_keeps_names_changes_and_pagination_readable():
    from goaletto.bot.training_views import training_report
    from goaletto.infrastructure.models import TrainingPlan

    week = datetime(2026, 10, 12, tzinfo=UTC)
    rows = [
        {
            "name": f"Игрок <{i}>&",
            "changes": {"pace": {"before": 10, "after": 11}},
            "progressed": True,
            "form_before": 6,
            "form_after": 7,
            "experience_before": 0,
            "experience_after": 0,
            "injured": False,
        }
        for i in range(6)
    ]
    rows[0]["changes"] = {}
    rows[0]["progressed"] = False
    rows[1]["injured"] = True
    plan = TrainingPlan(
        id=uuid4(), week_start=week, focus="physical", intensity="high", results=rows
    )
    text, kb = training_report(plan, "<Клуб>", 1)
    assert "&lt;Клуб&gt;" in text and "Игрок &lt;0&gt;&amp;" in text
    assert "Скорость: 10 → 11" in text and "Форма: 6 → 7" in text
    assert "Навыки не изменились" in text and "восстановление 7 дней" in text
    assert "Игрок &lt;5&gt;" not in text and len(text) < 4096
    assert any(b.text == "Следующая →" for row in kb.inline_keyboard for b in row)
    assert "Игрок &lt;5&gt;" in training_report(plan, "<Клуб>", 2)[0]
    with pytest.raises(ValueError):
        training_report(plan, "<Клуб>", 3)
