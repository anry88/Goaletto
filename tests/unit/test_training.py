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
    available_focus,
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
    budget = None
    for _ in range(60):
        result = train_player(
            p, Focus.PLAYMAKING, Intensity.MEDIUM, seed="week", trainer_skill=10, budget=budget
        )
        budget = result.budget
        p = replace(p, skills=result.skills, progress=result.progress, form=result.form)
    assert p.skills["playmaking"] == 11 and p.progress["playmaking"] == 2960
    capped = replace(
        p, skills={**p.skills, "playmaking": 15}, progress={**p.progress, "playmaking": 9900}
    )
    result = train_player(capped, Focus.PLAYMAKING, Intensity.HIGH, seed="week", trainer_skill=10)
    assert result.skills["playmaking"] == 15 and result.progress["playmaking"] == 0
    assert p.skills["defending"] == 10 and p.progress["defending"] == 0


def test_age_anchors_coaches_and_fractional_physical_gain():
    assert [age_multiplier(a) for a in (17, 22, 27, 32, 38)] == [15000, 12000, 8000, 3000, 3000]
    assert available_focus(Focus.ATTACKING, {"attack_coach": 20})
    assert not available_focus(Focus.DEFENDING, {"head_coach": 20, "attack_coach": 20})
    result = train_player(
        player(17), Focus.PHYSICAL, Intensity.HIGH, seed="first", trainer_skill=20
    )
    assert result.skills["stamina"] == 10 and result.skills["pace"] == 10
    assert result.progress["pace"] == result.progress["stamina"] == 263
    result = train_player(player(32), Focus.TEAM, Intensity.LOW, seed="first", trainer_skill=10)
    assert result.progress["experience"] == 6


def test_eligible_positions_executor_and_injury_skip():
    p = player(position="GK")
    assert not train_player(
        p, Focus.ATTACKING, Intensity.MEDIUM, seed="week", trainer_skill=10
    ).trained
    assert train_player(
        p, Focus.GOALKEEPING, Intensity.MEDIUM, seed="week", trainer_skill=10
    ).trained
    assert not train_player(
        p, Focus.SET_PIECES, Intensity.MEDIUM, seed="week", trainer_skill=10, executor_id=uuid4()
    ).trained
    assert train_player(
        p, Focus.SET_PIECES, Intensity.MEDIUM, seed="week", trainer_skill=10, executor_id=p.id
    ).trained
    result = train_player(
        replace(p, injured=True), Focus.PHYSICAL, Intensity.HIGH, seed="week", trainer_skill=10
    )
    assert not result.trained and result.skills == p.skills and result.progress == p.progress
    assert result.form == p.form and not result.injured


def test_seeded_risk_is_repeatable_and_physio_can_only_reduce_it():
    p = player()
    injury_seeds = [
        str(i)
        for i in range(10000)
        if train_player(p, Focus.PHYSICAL, Intensity.HIGH, seed=str(i), trainer_skill=10).injured
    ]
    assert 1 <= len(injury_seeds) <= 25
    for seed in injury_seeds:
        first = train_player(p, Focus.PHYSICAL, Intensity.HIGH, seed=seed, trainer_skill=10)
        assert first == train_player(p, Focus.PHYSICAL, Intensity.HIGH, seed=seed, trainer_skill=10)
        assert not train_player(
            p, Focus.PHYSICAL, Intensity.MEDIUM, seed=seed, trainer_skill=10
        ).injured
    for i in range(100):
        normal = train_player(p, Focus.PHYSICAL, Intensity.HIGH, seed=str(i), trainer_skill=10)
        reduced = train_player(
            p, Focus.PHYSICAL, Intensity.HIGH, seed=str(i), trainer_skill=10, physio_skill=20
        )
        assert not reduced.injured or normal.injured
    caps = initial_caps(p.id, p.skills)
    assert caps == initial_caps(p.id, p.skills) and all(12 <= v <= 20 for v in caps.values())


def test_week_callbacks_are_stable_and_fit_with_both_full_uuids():
    at = datetime(2026, 10, 18, 23, 59, tzinfo=UTC)
    week = week_start(at)
    assert week == datetime(2026, 10, 18, tzinfo=UTC)
    screen = TrainingScreen(
        "<Клуб>",
        uuid4(),
        week,
        week + timedelta(days=1),
        None,
        None,
        (TrainingMember(uuid4(), "<Игрок>", "MC"),),
        available=tuple(Focus),
    )
    for f in Focus:
        for i in Intensity:
            executor = screen.members[0].id if f == Focus.SET_PIECES else None
            data = choice_callback("s", screen, f, i, executor)
            assert len(data.encode()) <= 64
            assert parse_choice(data) == ("s", screen.ownership_id, week, f, i, executor)
    text, kb = training_view(screen, focus=Focus.SET_PIECES, intensity=Intensity.HIGH)
    assert "&lt;Klub&gt;" in text and "риск травмы" in text
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
    assert "&lt;Klub&gt;" in text and "Igrok &lt;0&gt;&amp;" in text
    assert "Скорость: 10 → 11" in text and "Форма: 6 → 7" in text
    assert "Навыки не изменились" in text and "восстановление 7 дней" in text
    assert "Igrok &lt;5&gt;" not in text and len(text) < 4096
    assert any(b.text == "Следующая →" for row in kb.inline_keyboard for b in row)
    assert "Igrok &lt;5&gt;" in training_report(plan, "<Клуб>", 2)[0]
    with pytest.raises(ValueError):
        training_report(plan, "<Клуб>", 3)


@pytest.mark.parametrize("language", ["ru", "en"])
def test_training_report_shows_frozen_position_each_skill_and_only_real_increases(language):
    from goaletto.bot.training_views import training_report
    from goaletto.infrastructure.models import TrainingPlan
    from goaletto.presentation.i18n import using_locale
    from tests.unit.test_language import assert_english

    row = {
        "name": "Player <One>",
        "position": "DC",
        "changes": {"stamina": {"before": 9, "after": 10}},
        "skill_progress": {
            "stamina": {
                "before": 9,
                "after": 10,
                "progress_before": 9750,
                "progress_after": 0,
                "gain_bps": 250,
            },
            "pace": {
                "before": 8,
                "after": 8,
                "progress_before": 2250,
                "progress_after": 2500,
                "gain_bps": 250,
            },
            "defending": {
                "before": 10,
                "after": 10,
                "progress_before": 123,
                "progress_after": 123,
                "gain_bps": 0,
            },
        },
        "form_before": 6,
        "form_after": 6,
        "experience_before": 0,
        "experience_after": 0,
        "leadership_before": 10,
        "leadership_after": 10,
        "leadership_gain": 50,
        "gain_bps": 500,
        "progressed": True,
        "injured": False,
    }
    plan = TrainingPlan(
        id=uuid4(),
        week_start=datetime(2026, 10, 31, tzinfo=UTC),
        focus="physical",
        intensity="medium",
        results=[row],
    )
    with using_locale(language):
        view = training_report(plan, "Club")
        text = view[0]
        assert "<b>Player &lt;One&gt;</b> · DC" in text
        assert text.count("9 → 10") == 1 and "8 → 8" not in text
        assert "Опыт:" not in text and "Experience:" not in text
        assert "накоплен" not in text and "accumulated" not in text
        assert "Развитие лидерства" not in text and "Leadership development" not in text
        if language == "ru":
            assert "Выносливость: 0/100 (+2,5%) · 9 → 10" in text
            assert "Скорость: 25/100 (+2,5%)" in text
            assert "Защита: 1,23/100 (+0%)" in text
        else:
            assert_english(view)
            assert "Stamina: 0/100 (+2.5%) · 9 → 10" in text
            assert "Pace: 25/100 (+2.5%)" in text
