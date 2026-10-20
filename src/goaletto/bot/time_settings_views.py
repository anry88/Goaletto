"""Explicit fixed UTC offsets. Time is supplied by the bot boundary."""

from datetime import datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from goaletto.domain.kickoff import validate_zone
from goaletto.presentation.i18n import tr

UTC_OFFSETS = tuple(range(-12, 15))


def offset_zone(hours: int) -> str:
    if type(hours) is not int or hours not in UTC_OFFSETS:
        raise ValueError("UTC offset must be a whole hour from -12 to +14")
    # IANA Etc/GMT names use the POSIX sign, opposite to the displayed UTC offset.
    return "UTC" if hours == 0 else f"Etc/GMT{-hours:+d}"


def timezone_label(zone: str) -> str:
    for hours in UTC_OFFSETS:
        if offset_zone(hours) == zone:
            return "UTC" if hours == 0 else f"UTC{hours:+d}"
    return zone  # An existing city preference remains explicit until the user changes it.


def offset_callback(hours: int) -> str:
    offset_zone(hours)
    return f"tz:offset:{hours}"


def parse_offset_callback(value: str) -> str:
    for hours in UTC_OFFSETS:
        if value == offset_callback(hours):
            return offset_zone(hours)
    raise ValueError("Invalid or obsolete timezone choice")


def utc_offset_view(at: datetime, *, stale: bool = False) -> tuple[str, InlineKeyboardMarkup]:
    if at.utcoffset() is None:
        raise ValueError("Timezone picker needs an aware observation time")
    lines = [
        tr("🌐 <b>Часовой пояс</b>"),
        tr("Выберите смещение от UTC. В скобках — местное время при открытии списка."),
        tr("Смещение фиксированное, без сезонного перевода часов."),
    ]
    if stale:
        lines.append(tr("Выбор устарел или недопустим. Выберите пояс из этого списка."))
    buttons = []
    for hours in UTC_OFFSETS:
        zone = offset_zone(hours)
        clock = at.astimezone(validate_zone(zone)).strftime("%H:%M")
        buttons.append(
            InlineKeyboardButton(
                text=f"{timezone_label(zone)} ({clock})", callback_data=offset_callback(hours)
            )
        )
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text=tr("🔄 Обновить"), callback_data="tz:open")])
    rows.append([InlineKeyboardButton(text=tr("← Настройки"), callback_data="ntf:open")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)
