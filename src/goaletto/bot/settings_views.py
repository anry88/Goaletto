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
from goaletto.bot.time_settings_views import timezone_label
from goaletto.domain.kickoff import local_datetime
from goaletto.domain.names import latin_name
from goaletto.presentation.i18n import tr

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
    status = tr("включены 🔔") if state.reminders_enabled else tr("выключены 🔕")
    lines = [tr("⚙️ <b>Настройки</b>"), "", tr("🔔 Напоминания: {p0}", p0=status)]
    if state.has_ownership and state.club_name is not None and state.expires_at is not None:
        lines.append(tr("Клуб: {p0}", p0=escape(latin_name(state.club_name))))
        lines.append(tr("Владение до: {p0}", p0=state.expires_at.strftime("%Y-%m-%d %H:%M UTC")))
        lines.append(tr("Напоминания: «Ваш клуб ждёт вас» до истечения владения."))
    else:
        lines.append(tr("Клуба пока нет. Откройте /start, чтобы получить клуб лиги 5."))
    if not state.bot_available:
        lines.append(
            tr("Отправка сейчас недоступна; доступность подтверждает /start, а не настройки.")
        )
    lines.extend(
        ("", tr("🌐 Часовой пояс: {zone}", zone=escape(timezone_label(state.timezone_name))))
    )
    if state.observed_at is not None:
        lines.append(
            tr(
                "Сейчас: {time}",
                time=escape(local_datetime(state.observed_at, state.timezone_name)),
            )
        )
    chosen = state.preferred_kickoff_minute
    if chosen is None:
        lines.append(tr("Время матча не выбрано: по умолчанию 18:00 UTC."))
    else:
        lines.append(
            tr("Удобное местное время матча: {time}", time=f"{chosen // 60:02}:{chosen % 60:02}")
        )
    lines.extend(
        (
            tr("Изменить время: /kickoff 19:30"),
            tr(
                "Назначенные матчи сохраняют время. "
                "Новое предпочтение учтётся в матчах за пределами ближайших 10 дней."
            ),
        )
    )
    buttons: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=tr("🌐 Выбрать часовой пояс"), callback_data="tz:open")]
    ]
    if state.has_ownership and state.ownership_id is not None:
        lines.append(
            tr("Переименование: одна попытка в запасе.")
            if state.rename_available
            else tr("Переименование: свободных попыток нет.")
        )
        lines.append(tr("Использовать попытку можно только в межсезонье."))
        if state.rename_available:
            buttons.append(
                [InlineKeyboardButton(text=tr("Название клуба"), callback_data="rn:open")]
            )
        else:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=tr("Купить попытку · 50 ⭐"),
                        callback_data=f"buy:rename:{compact_id(state.ownership_id)}",
                    )
                ]
            )
        if state.vip_until:
            lines.append(
                tr("👑 VIP до {date} UTC", date=state.vip_until.strftime("%d.%m.%Y %H:%M"))
            )
        else:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=tr("👑 VIP на календарный месяц · 1 ⭐"),
                        callback_data=f"buy:vip:{compact_id(state.ownership_id)}",
                    )
                ]
            )
        lines.append(
            tr(
                "VIP — видимый другим статус клуба и способ поддержать проект. "
                "Корона и VIP, одна попытка переименования в межсезонье при пустом запасе. "
                "Других бонусов нет. Без автопродления."
            )
        )
        buttons.append(
            [InlineKeyboardButton(text=tr("Поддержка платежей"), callback_data="pay:support")]
        )
        if state.reminders_enabled:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=tr("🔕 Выключить напоминания"),
                        callback_data=disable_callback(state.ownership_id),
                    )
                ]
            )
        else:
            buttons.append(
                [
                    InlineKeyboardButton(
                        text=tr("🔔 Включить напоминания"),
                        callback_data=enable_callback(state.ownership_id),
                    )
                ]
            )
        buttons.append([InlineKeyboardButton(text=tr("🏟 Мой клуб"), callback_data="vc:c")])
    buttons.append([InlineKeyboardButton(text=tr("🏠 Главное меню"), callback_data="vc:menu")])
    lines.append("\n" + tr("Язык / Language"))
    buttons.insert(
        0,
        [
            InlineKeyboardButton(
                text=("✓ " if state.locale == "ru" else "") + "Русский", callback_data="lang:ru"
            ),
            InlineKeyboardButton(
                text=("✓ " if state.locale == "en" else "") + "English", callback_data="lang:en"
            ),
        ],
    )
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


def stale_settings_view() -> tuple[str, InlineKeyboardMarkup]:
    text = tr("Кнопка устарела или клуб больше не принадлежит вам. Откройте /settings заново.")
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=tr("🏟 Мой клуб"), callback_data="vc:c")],
            [InlineKeyboardButton(text=tr("🏠 Главное меню"), callback_data="vc:menu")],
        ]
    )
    return text, keyboard
