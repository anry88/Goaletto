"""UTC picker sign, day-boundary and stale-choice contracts."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from goaletto.bot.settings_views import settings_view
from goaletto.bot.time_settings_views import (
    UTC_OFFSETS,
    kickoff_time_view,
    offset_callback,
    offset_zone,
    parse_kickoff_callback,
    parse_offset_callback,
    utc_offset_view,
)
from goaletto.domain.kickoff import KickoffPreference, preference_utc, validate_zone
from goaletto.presentation.i18n import using_locale
from tests.unit.test_settings_views import enabled_state


def test_kickoff_picker_has_every_half_hour_and_keeps_language_independent_callbacks():
    callbacks = []
    for language in ("ru", "en"):
        with using_locale(language):
            text, keyboard = kickoff_time_view("Etc/GMT-2", 1170)
            settings, settings_keyboard = settings_view(
                replace(enabled_state(), timezone_name="Etc/GMT-2", preferred_kickoff_minute=1170)
            )
        choices = [b for row in keyboard.inline_keyboard[:-1] for b in row]
        assert len(choices) == 48
        assert [parse_kickoff_callback(b.callback_data) for b in choices] == list(
            range(0, 1440, 30)
        )
        assert [b.text for b in choices if b.text.startswith("✓")] == ["✓ 19:30"]
        assert "UTC+2" in text and "19:30" in text and "19:30" in settings
        assert len(text) < 4096
        assert all(len(b.callback_data.encode()) <= 64 for b in choices)
        assert any(
            b.callback_data == "ko:open" for row in settings_keyboard.inline_keyboard for b in row
        )
        callbacks.append([b.callback_data for b in choices])
        with using_locale(language):
            default, _ = kickoff_time_view("UTC", None)
        assert "18:00 UTC" in default
    assert callbacks[0] == callbacks[1]
    for value in (
        "ko:minute:1",
        "ko:minute:-30",
        "ko:minute:1440",
        "ko:minute:030",
        "ko:minute:30:extra",
    ):
        with pytest.raises(ValueError):
            parse_kickoff_callback(value)


async def test_kickoff_button_commits_before_display_and_reopens_saved_choice(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from aiogram.types import CallbackQuery, Chat, Message, User

    from goaletto.bot import settings_router

    events = []
    saved = enabled_state()
    session, actor = object(), object()

    @asynccontextmanager
    async def transaction():
        events.append("begin")
        yield session
        events.append("commit")

    async def save(actual_session, actual_actor, *, minute):
        nonlocal saved
        assert (actual_session, actual_actor) == (session, actor)
        saved = replace(saved, preferred_kickoff_minute=minute)

    async def settings(*args):
        return saved

    async def edit(message, text, keyboard):
        assert events[-1] == "commit"
        assert "19:30" in text
        events.append(keyboard)

    monkeypatch.setattr(CallbackQuery, "answer", AsyncMock())
    monkeypatch.setattr(settings_router, "actor_for_callback", AsyncMock(return_value=actor))
    monkeypatch.setattr(settings_router, "get_settings", settings)
    monkeypatch.setattr(settings_router, "edit_response", edit)
    setter = AsyncMock(side_effect=save)
    monkeypatch.setattr("goaletto.application.kickoff_preferences.set_kickoff_preference", setter)
    user = User(id=123, is_bot=False, first_name="Manager")
    message = Message(
        message_id=1, date=datetime(2026, 10, 22, tzinfo=UTC), chat=Chat(id=123, type="private")
    )
    query = CallbackQuery(
        id="save", from_user=user, chat_instance="private", message=message, data="ko:minute:1170"
    )
    database = SimpleNamespace(transaction=transaction)
    await settings_router.kickoff_callback(query, database)
    await settings_router.kickoff_callback(
        query.model_copy(update={"id": "open", "data": "ko:open"}), database
    )
    setter.assert_awaited_once_with(session, actor, minute=1170)
    assert any(b.text == "✓ 19:30" for row in events[-1].inline_keyboard for b in row)


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
