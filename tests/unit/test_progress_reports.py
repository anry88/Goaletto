from datetime import UTC, datetime
from uuid import uuid4

import pytest

from goaletto.application.history import ReportLineupPlayer
from goaletto.bot.history_cards import _player_progress
from goaletto.bot.training_views import training_report
from goaletto.infrastructure.models import TrainingPlan
from goaletto.presentation.i18n import using_locale
from goaletto.presentation.progress import experience_line, progress_line


@pytest.mark.parametrize("language", ["ru", "en"])
def test_identical_skill_experience_and_leadership_units_across_reports(language):
    with using_locale(language):
        skill = progress_line("Skill", 2500, 250, 9, 10)
        assert skill == (
            "Skill: 25/100 (+2,5%) · 9 → 10"
            if language == "ru"
            else "Skill: 25/100 (+2.5%) · 9 → 10"
        )
        assert experience_line("XP", 9900, 10150, 250) == (
            "XP: 1,5/100 (+2,5%) · 0 → 1" if language == "ru" else "XP: 1.5/100 (+2.5%) · 0 → 1"
        )
        member = ReportLineupPlayer(
            uuid4(),
            "Player",
            22,
            6,
            "MC",
            True,
            90,
            0,
            0,
            False,
            False,
            growth_bps=250,
            skill_gains=(("playmaking", 250),),
            skill_progress=(("playmaking", 9, 10, 2500),),
            position_experience=(("MC", 9900, 10150), ("FC", 100, 400)),
        )
        body = _player_progress(member)
        assert (
            "Плеймейкинг: 25/100 (+2,5%) · 9 → 10"
            if language == "ru"
            else "Playmaking: 25/100 (+2.5%) · 9 → 10"
        ) in body
        assert (
            "Опыт MC: 1,5/100 (+2,5%) · 0 → 1"
            if language == "ru"
            else "Experience MC: 1.5/100 (+2.5%) · 0 → 1"
        ) in body
        assert (
            "Опыт FC: 4/100 (+3%)" if language == "ru" else "Experience FC: 4/100 (+3%)"
        ) in body
        row = {
            "name": "Player",
            "position": "MC",
            "changes": {},
            "skill_progress": {
                "playmaking": {"before": 9, "after": 10, "progress_after": 2500, "gain_bps": 250}
            },
            "form_before": 6,
            "form_after": 6,
            "experience_before": 0,
            "experience_after": 1,
            "position_experience": {"MC": {"before": 9900, "after": 10150}},
            "leadership_before": 9,
            "leadership_after": 10,
            "leadership_gain": 5,
            "leadership_progress": {"after": 3},
            "injured": False,
        }
        plan = TrainingPlan(
            id=uuid4(),
            week_start=datetime(2026, 11, 1, tzinfo=UTC),
            focus="team",
            intensity="medium",
            results=[row],
        )
        trained, _ = training_report(plan, "Club")
        assert (
            "Лидерство: 0,03/100 (+0,05%) · 9 → 10"
            if language == "ru"
            else "Leadership: 0.03/100 (+0.05%) · 9 → 10"
        ) in trained
        for line in body.splitlines():
            if "/100" in line:
                if "FC:" not in line:
                    assert line in trained
        assert "10 → 10" not in trained


@pytest.mark.parametrize("language", ["ru", "en"])
def test_percent_position_experience_shows_small_gains_and_inactivity_losses(language):
    with using_locale(language):
        member = ReportLineupPlayer(
            uuid4(),
            "Player",
            32,
            6,
            "MC",
            True,
            90,
            0,
            0,
            False,
            False,
            growth_bps=0,
            position_minutes=(("MC", 90),),
            position_experience=(("MC", 0, 30), ("FC", 500000, 497000)),
            experience_percent=True,
        )
        body = _player_progress(member)
        expected = (
            "Опыт MC: 0,003% (+0,003%)" if language == "ru" else "Experience MC: 0.003% (+0.003%)"
        )
        loss = "Опыт FC: 49,7% (-0,3%)" if language == "ru" else "Experience FC: 49.7% (-0.3%)"
        assert expected in body and loss in body
        row = {
            "name": "Player",
            "position": "MC",
            "changes": {},
            "skill_progress": {},
            "form_before": 6,
            "form_after": 6,
            "experience_before": 0,
            "experience_after": 0,
            "experience_policy": "position-experience-v2",
            "position_experience": {"MC": {"before": 0, "after": 30}},
            "injured": False,
        }
        plan = TrainingPlan(
            id=uuid4(),
            week_start=datetime(2026, 11, 1, tzinfo=UTC),
            focus="team",
            intensity="medium",
            results=[row],
        )
        trained, _ = training_report(plan, "Club")
        assert expected in trained
