"""Settings presenter and callback tests. No database, no network."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from goaletto.application.notification_preferences import SettingsState
from goaletto.bot.settings_views import (
    disable_callback,
    enable_callback,
    is_settings_callback,
    parse_settings_callback,
    settings_callback,
    settings_view,
    stale_settings_view,
)

NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)


def enabled_state() -> SettingsState:
    return SettingsState(
        manager_id=uuid4(),
        telegram_id=123,
        reminders_enabled=True,
        bot_available=True,
        has_ownership=True,
        ownership_id=uuid4(),
        club_id=uuid4(),
        club_name="<Аврора>&",
        expires_at=datetime(2027, 4, 11, 12, tzinfo=UTC),
        last_game_action_at=NOW,
    )


def test_explicit_callbacks_round_trip_and_never_toggle() -> None:
    ownership = uuid4()
    on, off = enable_callback(ownership), disable_callback(ownership)
    assert parse_settings_callback(on) == ("on", ownership)
    assert parse_settings_callback(off) == ("off", ownership)
    assert parse_settings_callback(settings_callback()) == ("open", None)
    # Repeating the same explicit callback resolves to the same desired value.
    assert parse_settings_callback(on) == ("on", ownership)
    with pytest.raises(ValueError):
        parse_settings_callback("ntf:toggle")
    with pytest.raises(ValueError):
        parse_settings_callback("vc:c")
    assert is_settings_callback(on) and not is_settings_callback("vc:c")


def test_settings_hide_lease_details_and_offer_no_reminder_controls() -> None:
    text, keyboard = settings_view(enabled_state())
    assert text.startswith("⚙️ <b>Настройки</b>")
    assert "Напоминания" not in text and "Владение до" not in text
    assert "2027-04-11" not in text
    callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert not any(c is not None and c.startswith("ntf:") for c in callbacks)
    assert "vc:c" in callbacks and "vc:menu" in callbacks


def test_legacy_disabled_flag_has_no_user_control() -> None:
    state = enabled_state()
    disabled = SettingsState(
        manager_id=state.manager_id,
        telegram_id=state.telegram_id,
        reminders_enabled=False,
        bot_available=state.bot_available,
        has_ownership=True,
        ownership_id=state.ownership_id,
        club_id=state.club_id,
        club_name=state.club_name,
        expires_at=state.expires_at,
        last_game_action_at=state.last_game_action_at,
    )
    text, keyboard = settings_view(disabled)
    assert "Напоминания" not in text
    callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert not any(c is not None and c.startswith("ntf:on:") for c in callbacks)
    assert not any(c is not None and c.startswith("ntf:off:") for c in callbacks)


def test_view_without_ownership_and_unavailable_bot() -> None:
    state = SettingsState(
        manager_id=uuid4(),
        telegram_id=123,
        reminders_enabled=False,
        bot_available=False,
        has_ownership=False,
        ownership_id=None,
        club_id=None,
        club_name=None,
        expires_at=None,
        last_game_action_at=None,
    )
    text, keyboard = settings_view(state)
    assert "/start" in text and "доступность подтверждает" not in text
    callbacks = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert "vc:menu" in callbacks and "vc:c" not in callbacks
    stale_text, _ = stale_settings_view()
    assert "/settings" in stale_text
