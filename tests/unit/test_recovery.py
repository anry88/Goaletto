from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from goaletto.application.schedule import ScheduleDay, ScheduleEntry, ScheduleResult, ScheduleScreen
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
        assert "Opponent TBD" in view[0] and "Second leg" in view[0]
        assert "Kickoff TBD" not in view[0] and "🕒" not in view[0]
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


@pytest.mark.parametrize("language", ["ru", "en"])
def test_schedule_marks_completed_training_and_finished_match(language):
    day = datetime(2026, 10, 31, tzinfo=UTC)
    entries = (
        ScheduleEntry(
            uuid4(),
            day.date(),
            "training",
            0,
            None,
            None,
            "home",
            day,
            False,
            None,
            None,
            "applied",
            "physical",
            "medium",
        ),
        ScheduleEntry(
            uuid4(),
            day.date(),
            "league",
            1,
            "Home",
            "Away",
            "home",
            day,
            False,
            None,
            None,
            "finished",
        ),
    )
    screen = ScheduleScreen(
        uuid4(), "Club", "UTC", (ScheduleDay(day.date(), "league", 1, entries),), 1, 1
    )
    with using_locale(language):
        view = schedule_view(screen)
        if language == "en":
            assert_english(view)
            assert "✅ Training completed" in view[0] and "✅ Match finished" in view[0]
        else:
            assert "✅ Тренировка проведена" in view[0] and "✅ Матч завершён" in view[0]


@pytest.mark.parametrize(
    ("language", "extra", "aggregate", "penalties", "advances", "winner"),
    (
        ("ru", "Дополнительное время", "Общий счёт", "Пенальти", "Прошёл дальше", "Победитель"),
        ("en", "Extra time", "Aggregate", "Penalties", "Advances", "Winner"),
    ),
)
def test_schedule_uses_real_scores_and_labels_only_decided_knockout_winners(
    language, extra, aggregate, penalties, advances, winner
):
    kickoff = datetime(2026, 11, 26, 18, tzinfo=UTC)
    first = ScheduleEntry(
        uuid4(),
        kickoff.date(),
        "cup",
        4,
        "Own",
        "<Rival>",
        "home",
        kickoff,
        False,
        "quarter",
        1,
        "finished",
        result=ScheduleResult((2, 1)),
    )
    second_day = kickoff + timedelta(days=4)
    second = replace(
        first,
        id=uuid4(),
        day=second_day.date(),
        home="<Rival>",
        away="Own",
        venue="away",
        kickoff=second_day,
        leg=2,
        result=ScheduleResult((2, 1), (2, 1), (3, 3), True, (5, 4), "<Rival>"),
    )
    final = replace(
        second,
        id=uuid4(),
        competition="champions",
        stage="final",
        leg=1,
        venue="neutral",
        result=ScheduleResult((0, 0), extra_time=True, penalties=(4, 2), winner="<Rival>"),
    )
    known = replace(first, kickoff=None, status="unannounced", result=None)
    unknown = replace(known, home=None, away=None, conditional=True)
    screen = ScheduleScreen(uuid4(), "Own", "Europe/Belgrade", (), 1, 1, "cup")

    def render(*entries):
        days = tuple(ScheduleDay(e.day, e.competition, 1, (e,)) for e in entries)
        return schedule_view(replace(screen, days=days))

    with using_locale(language):
        pending, _ = render(known, unknown)
        assert "Own — &lt;Rival&gt;" in pending and "🕒" not in pending
        assert pending.count("Kickoff TBD" if language == "en" else "Время не назначено") == 1
        assert ("Teams TBD" if language == "en" else "Участники не определены") in pending

        first_only, _ = render(first)
        assert "Own <b>2:1</b> &lt;Rival&gt;" in first_only
        assert f"{advances}:" not in first_only and f"{aggregate}:" not in first_only
        pair = render(first, second)
        if language == "en":
            assert_english(pair)
        assert "&lt;Rival&gt; <b>2:1</b> Own" in pair[0]
        assert "Own <b>2:1</b> &lt;Rival&gt;" in pair[0]
        assert f"{aggregate}: 3:3" in pair[0]
        assert f"{penalties}: 5:4" in pair[0]
        assert extra in pair[0] and f"{advances}: &lt;Rival&gt;" in pair[0]
        assert "19:00" in pair[0]

        final_text, _ = render(final)
        assert "&lt;Rival&gt; <b>0:0</b> Own" in final_text
        assert f"{penalties}: 4:2" in final_text and f"{winner}: &lt;Rival&gt;" in final_text
        assert f"{aggregate}:" not in final_text and f"{advances}:" not in final_text
        assert ("First leg" if language == "en" else "Первый матч") not in final_text

        calendar = replace(
            screen,
            competition=None,
            tournaments=("cup",),
            days=(ScheduleDay(second.day, "cup", 2, (second,)),),
        )
        cal_text, _ = schedule_view(calendar)
        assert "Own 2:1 &lt;Rival&gt;" in cal_text and f"{advances}: &lt;Rival&gt;" in cal_text
        _, keys = schedule_view(replace(calendar, days=()))
        assert any(
            b.callback_data.startswith("ca:cup:") for row in keys.inline_keyboard for b in row
        )
