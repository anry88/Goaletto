"""UTC picker sign, day-boundary and stale-choice contracts."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from goaletto.bot.settings_views import settings_view
from goaletto.bot.time_settings_views import (
    UTC_OFFSETS,
    offset_callback,
    offset_zone,
    parse_offset_callback,
    utc_offset_view,
)
from goaletto.domain.kickoff import KickoffPreference, preference_utc, validate_zone
from goaletto.presentation.i18n import using_locale
from tests.unit.test_settings_views import enabled_state


def test_all_offsets_show_the_same_instant_and_use_correct_iana_sign():
    at = datetime(2026, 10, 20, 9, 2, tzinfo=UTC)
    for language in ("ru", "en"):
        with using_locale(language):
            text, keyboard = utc_offset_view(at)
        choices = [
            b
            for row in keyboard.inline_keyboard
            for b in row
            if b.callback_data.startswith("tz:offset:")
        ]
        assert len(choices) == len(set(b.callback_data for b in choices)) == 27
        assert [int(b.callback_data.rsplit(":", 1)[1]) for b in choices] == list(range(-12, 15))
        by_offset = dict(zip(UTC_OFFSETS, choices, strict=True))
        assert by_offset[1].text == "UTC+1 (10:02)"
        assert by_offset[-2].text == "UTC-2 (07:02)"
        assert by_offset[0].text == "UTC (09:02)"
        assert by_offset[-12].text == "UTC-12 (21:02)"
        assert by_offset[14].text == "UTC+14 (23:02)"
        for offset, button in by_offset.items():
            zone = parse_offset_callback(button.callback_data)
            assert at.astimezone(validate_zone(zone)).utcoffset() == timedelta(hours=offset)
            assert len(button.callback_data.encode()) <= 64
        assert "Etc/" not in text and "/timezone Belgrade" not in text
        assert keyboard.inline_keyboard[-2][0].callback_data == "tz:open"
        assert keyboard.inline_keyboard[-1][0].callback_data == "ntf:open"


def test_midnight_rollover_and_fixed_offset_stays_fixed_across_dst():
    _, keyboard = utc_offset_view(datetime(2026, 10, 20, 23, 50, tzinfo=UTC))
    assert any(b.text == "UTC+1 (00:50)" for row in keyboard.inline_keyboard for b in row)
    for hours in UTC_OFFSETS:
        for day in (date(2026, 1, 1), date(2026, 7, 1)):
            result = preference_utc(day, KickoffPreference(offset_zone(hours), 19 * 60 + 17))
            assert result.date() == day
            assert result.astimezone(validate_zone(offset_zone(hours))).strftime("%H:%M") == "19:17"
            assert result.hour == (19 - hours) % 24
        text, _ = settings_view(replace(enabled_state(), timezone_name=offset_zone(hours)))
        assert "Etc/GMT" not in text


def test_noncanonical_and_obsolete_choices_cannot_be_saved_as_offsets():
    for value in (
        "tz:Europe/Belgrade",
        "tz:UTC",
        "tz:offset:+1",
        "tz:offset:-0",
        "tz:offset:01",
        "tz:offset:15",
        "tz:offset:-13",
        "tz:offset:5.5",
        "tz:offset:1:extra",
    ):
        with pytest.raises(ValueError):
            parse_offset_callback(value)
    for value in (True, 5.5, -13, 15):
        with pytest.raises(ValueError):
            offset_callback(value)
    with pytest.raises(ValueError):
        utc_offset_view(datetime(2026, 10, 20))
