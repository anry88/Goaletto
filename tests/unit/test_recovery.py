from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from goaletto.application.schedule import ScheduleDay, ScheduleEntry, ScheduleScreen
from goaletto.bot.schedule_views import schedule_view
from goaletto.domain.recovery import daily_recovery, ready_in, recover
from goaletto.presentation.i18n import using_locale
from tests.unit.test_language import assert_english


def test_average_recovery_and_staff_calibration():
    after_match = 10000 - (50 - 2 * 9) * 90 + 200
    assert after_match == 7320
    average = daily_recovery(9, 27, 10)
    assert recover(after_match, 0, 86400, average)[0] == 10000
    assert recover(after_match, 0, 3600, average)[0] < 7500
    assert recover(after_match, 0, 86400, daily_recovery(9, 27, 0))[0] == 9120
    assert daily_recovery(9, 27, 2) < average < daily_recovery(9, 27, 18)
    assert daily_recovery(9, 38, 10) < average < daily_recovery(15, 22, 10)
    assert 22 * 3600 < ready_in(after_match, 0, average) < 24 * 3600


def test_fractional_recovery_is_independent_of_read_frequency():
    energy, fraction = 5000, 0
    for _ in range(3600):
        energy, fraction = recover(energy, fraction, 1, 2800)
    assert (energy, fraction) == recover(5000, 0, 3600, 2800)
    with pytest.raises(ValueError):
        recover(5000, 0, -1, 2800)


@pytest.mark.parametrize("rest_hours", [1, 8, 16, 24])
def test_consecutive_tournament_days_and_extra_time(rest_hours):
    energy = 10000
    for minutes in (90, 120, 90):
        energy = max(0, energy - 32 * minutes + 200)
        energy, _ = recover(energy, 0, rest_hours * 3600, daily_recovery(9, 27, 10))
        assert 0 <= energy <= 10000
    if rest_hours < 24:
        assert energy < 10000


def test_schedule_english_conditional_neutral_and_local_day():
    day = datetime(2026, 10, 17, tzinfo=UTC)
    entry = ScheduleEntry(
        uuid4(), day.date(), "cup", 1, "<Club>", None, "neutral", None, True, None, 2, "unannounced"
    )
    screen = ScheduleScreen(
        uuid4(), "<Club>", "Europe/Belgrade", (ScheduleDay(day.date(), "cup", 1, (entry,)),), 1, 3
    )
    with using_locale("en"):
        view = schedule_view(screen)
        assert_english(view)
        assert "Opponent to be decided" in view[0] and "Second leg" in view[0]
        assert "Neutral ground" in view[0] and "&lt;Club&gt;" in view[0]
        assert all(
            len(b.callback_data.encode()) <= 64 for row in view[1].inline_keyboard for b in row
        )
    announced = ScheduleEntry(
        uuid4(),
        day.date(),
        "league",
        2,
        "Home",
        "Away",
        "home",
        day + timedelta(hours=23),
        False,
        None,
        None,
        "scheduled",
    )
    screen = ScheduleScreen(
        screen.ownership_id,
        "Club",
        "Europe/Belgrade",
        (ScheduleDay(day.date(), "league", 1, (announced,)),),
        1,
        1,
    )
    assert "18.10" in schedule_view(screen)[0]
