from datetime import UTC, datetime
from uuid import uuid4

import pytest

from goaletto.application.training_reports import _historical_fraction, _session_gains
from goaletto.bot.training_views import training_report
from goaletto.domain.development import BUDGET_KEYS
from goaletto.infrastructure.models import PlayerDevelopment, TrainingPlan


def budget(**values):
    return {**dict.fromkeys(BUDGET_KEYS, 0), **values}


def test_old_fraction_excludes_later_training_and_matches_and_survives_whole_point():
    row = {"development_age": 22, "season_gains": budget(attacking=2500, training=2500)}
    dev = PlayerDevelopment(
        growth_age=22,
        season_gains=budget(attacking=11000, training=4000, match=7000),
        progress={"attacking": 1000},
    )
    assert _historical_fraction(row, dev, "attacking") == 2500
    dev.growth_age = 23
    assert _historical_fraction(row, dev, "attacking") is None
    dev.growth_age = 22
    dev.season_gains = budget(attacking=2000, training=2000)
    assert _historical_fraction(row, dev, "attacking") is None


def test_old_physical_gains_keep_unequal_capped_shares_and_require_exact_budget():
    previous = budget(stamina=1400, pace=2500, training=3900)
    row = {
        "gain_bps": 251,
        "development_age": 22,
        "season_gains": budget(stamina=1401, pace=2750, training=4151),
    }
    old = (datetime(2026, 10, 30, tzinfo=UTC), 22, previous)
    assert _session_gains(row, "physical", "DC", old) == {"stamina": 1, "pace": 250}
    assert _session_gains(row, "physical", "DC", None) is None
    row["gain_bps"] = 250
    assert _session_gains(row, "physical", "DC", old) is None


@pytest.mark.parametrize(
    "skill", ["goalkeeping", "defending", "playmaking", "winger", "attacking", "set_pieces"]
)
def test_old_specialist_reports_keep_each_actual_skill_gain(skill):
    assert _session_gains({"gain_bps": 224}, skill, "DC", None) == {skill: 224}


def test_legacy_view_never_calls_positive_fractional_gain_unchanged():
    plan = TrainingPlan(
        id=uuid4(),
        week_start=datetime(2026, 10, 31, tzinfo=UTC),
        focus="attacking",
        intensity="medium",
        results=[
            {
                "name": "Player",
                "changes": {},
                "gain_bps": 224,
                "form_before": 6,
                "form_after": 6,
                "experience_before": 0,
                "experience_after": 0,
                "injured": False,
            }
        ],
    )
    body, _ = training_report(plan, "Club")
    assert "+2,24%" in body and "Навыки не изменились" not in body
