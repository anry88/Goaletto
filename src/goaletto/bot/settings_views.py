"""Pure /settings presenters and callbacks. No database, network or wall-clock access.

Callbacks carry the explicit desired reminder value plus the ownership they
were rendered for; the application layer rechecks that ownership under lock.
Retried callbacks resolve to the same desired value and never toggle back.
Dynamic names are HTML-escaped. Result/live-score delivery rules are untouched.
"""

from html import escape
from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from goaletto.application.notification_preferences import SettingsState
from goaletto.bot.result_views import compact_id, expand_id

SETTINGS_OPEN = "ntf:open"


def settings_callback() -> str:
    return SETTINGS_OPEN


def enable_callback(ownership_id: UUID) -> str:
    if not isinstance(ownership_id, UUID):
        raise ValueError("Invalid ownership identity")
    value = f"ntf:on:{compact_id(ownership_id)}"
    assert len(value.encode()) <= 64
    return value


def disable_callback(ownership_id: UUID) -> str:
    if not isinstance(ownership_id, UUID):
        raise ValueError("Invalid ownership identity")
    value = f"ntf:off:{compact_id(ownership_id)}"
    assert len(value.encode()) <= 64
    return value


def parse_settings_callback(value: str) -> tuple[str, UUID | None]:
    """Return (action, ownership_id) for our prefix; raise on anything else."""
    if type(value) is not str:
        raise ValueError("Invalid settings callback")
    if value == SETTINGS_OPEN:
        return ("open", None)
    parts = value.split(":")
    if len(parts) == 3 and parts[0] == "ntf" and parts[1] in {"on", "off"}:
        return (parts[1], expand_id(parts[2]))
    raise ValueError("Invalid settings callback")


def is_settings_callback(value: str | None) -> bool:
    return type(value) is str and (value == SETTINGS_OPEN or value.startswith("ntf:"))


def settings_view(state: SettingsState) -> tuple[str, InlineKeyboardMarkup]:
    if not isinstance(state, SettingsState):
        raise ValueError("Invalid settings state")
    status = "включены 🔔" if state.reminders_enabled else "выключены 🔕"
    lines = ["🔔 <b>Напоминания</b>", f"Статус: {status}"]
    if state.has_ownership and state.club_name is not None and state.expires_at is not None:
        lines.append(f"Клуб: {escape(state.club_name)}")
        lines.append(f"Владение до: {state.expires_at.strftime('%Y-%m-%d %H:%M UTC')}")
        lines.append("Напоминания: «Ваш клуб ждёт вас» до истечения владения.")
    else:
        lines.append("Клуба пока нет. Откройте /start, чтобы получить клуб лиги 5.")
    if not state.bot_available:
        lines.append("Отправка сейчас недоступна; доступность подтверждает /start, а не настройки.")
    buttons: list[list[InlineKeyboardButton]] = []
    if state.has_ownership and state.ownership_id is not None:
        if state.reminders_enabled:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="🔕 Выключить напоминания",
                        callback_data=disable_callback(state.ownership_id),
                    )
                ]
            )
        else:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="🔔 Включить напоминания",
                        callback_data=enable_callback(state.ownership_id),
                    )
                ]
            )
        buttons.append([InlineKeyboardButton(text="🏟 Мой клуб", callback_data="vc:c")])
    buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="vc:menu")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


def stale_settings_view() -> tuple[str, InlineKeyboardMarkup]:
    text = "Кнопка устарела или клуб больше не принадлежит вам. Откройте /settings заново."
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏟 Мой клуб", callback_data="vc:c")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="vc:menu")],
        ]
    )
    return text, keyboard
