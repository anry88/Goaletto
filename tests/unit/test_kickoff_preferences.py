from datetime import UTC, date, datetime
from itertools import product

import pytest

from goaletto.domain.kickoff import (
    KickoffPreference as Pref,
)
from goaletto.domain.kickoff import (
    choose_kickoff,
    local_datetime,
    parse_local_time,
    preference_utc,
    search_zones,
)

DAY = date(2026, 10, 17)


@pytest.mark.parametrize(
    "a,b,expected", [(840, 960, 900), (840, 900, 870), (1380, 60, 0), (840, 870, 870), (0, 0, 0)]
)
def test_confirmed_short_arc_and_round_half_forward(a, b, expected):
    result = choose_kickoff(DAY, Pref("UTC", a), Pref("UTC", b), seed=2)
    assert result.kickoff == datetime(2026, 10, 17, *divmod(expected, 60), tzinfo=UTC)


def test_all_clock_pairs_symmetrical_and_inside_day():
    for a, b, seed in product(range(0, 1440, 30), range(0, 1440, 30), (0, 1)):
        result = choose_kickoff(DAY, Pref("UTC", a), Pref("UTC", b), seed=seed)
        assert result == choose_kickoff(DAY, Pref("UTC", b), Pref("UTC", a), seed=seed)
        assert result.kickoff.date() == DAY and result.kickoff.minute in (0, 30)
    first = choose_kickoff(DAY, Pref("UTC", 0), Pref("UTC", 720), seed=0)
    second = choose_kickoff(DAY, Pref("UTC", 0), Pref("UTC", 720), seed=1)
    assert (first.tie_break, second.tie_break) == (0, 1)
    assert (first.kickoff.hour, second.kickoff.hour) == (6, 18)


@pytest.mark.parametrize(
    "zone,local,utc",
    [
        ("Asia/Kathmandu", "19:00", "13:15"),
        ("Asia/Kolkata", "19:00", "13:30"),
        ("Pacific/Chatham", "19:00", "05:15"),
        ("Pacific/Kiritimati", "01:00", "11:00"),
        ("Etc/GMT+12", "23:00", "11:00"),
    ],
)
def test_human_vs_bot_preserves_local_minutes_and_extreme_offsets(zone, local, utc):
    pref = Pref(zone, parse_local_time(local))
    result = choose_kickoff(DAY, pref, None, seed=0)
    assert result.kickoff.strftime("%H:%M") == utc
    assert result.kickoff.date() == DAY
    assert choose_kickoff(DAY, None, pref, seed=0) == result
    if zone in {"Pacific/Kiritimati", "Etc/GMT+12"}:
        assert " · UTC 17.10.2026" in local_datetime(result.kickoff, zone)


def test_dst_uses_fixture_date_and_handles_gap_and_fold():
    assert preference_utc(date(2026, 1, 1), Pref("Europe/Belgrade", 19 * 60)).hour == 18
    assert preference_utc(date(2026, 7, 1), Pref("Europe/Belgrade", 19 * 60)).hour == 17
    assert preference_utc(date(2026, 3, 29), Pref("Europe/Belgrade", 150)) == datetime(
        2026, 3, 29, 1, 30, tzinfo=UTC
    )
    assert preference_utc(date(2026, 10, 25), Pref("Europe/Belgrade", 150)) == datetime(
        2026, 10, 25, 0, 30, tzinfo=UTC
    )


def test_baseline_search_and_invalid_inputs():
    assert choose_kickoff(DAY, None, None, seed=0).kickoff.hour == 18
    assert search_zones("belgrade") == ("Europe/Belgrade",)
    assert "America/New_York" in search_zones("New York")
    for value in ("24:00", "9:00", "12:60", "１２:００", "x"):
        with pytest.raises(ValueError):
            parse_local_time(value)
    with pytest.raises(ValueError):
        Pref("not/a/zone", 10)
    with pytest.raises(ValueError):
        Pref("UTC", True)


def test_bundled_tzdata_without_system_timezone_database():
    import zoneinfo

    try:
        zoneinfo.reset_tzpath(())
        zoneinfo.ZoneInfo.clear_cache()
        result = choose_kickoff(DAY, Pref("Asia/Kathmandu", 1140), None, seed=0)
        assert result.kickoff.hour == 13 and result.kickoff.minute == 15
        assert search_zones("Belgrade") == ("Europe/Belgrade",)
    finally:
        zoneinfo.reset_tzpath()
        zoneinfo.ZoneInfo.clear_cache()
