"""Explicit fixed UTC offsets. Time is supplied by the bot boundary."""

from datetime import datetime
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from goaletto.domain.kickoff import validate_zone
from goaletto.presentation.i18n import tr

UTC_OFFSETS = tuple(range(-12, 15))
KICKOFF_MINUTES = tuple(range(0, 1440, 30))


def parse_kickoff_callback(value: str) -> int:
    for minute in KICKOFF_MINUTES:
        if value == f"ko:minute:{minute}":
            return minute
    raise ValueError("Invalid kickoff choice")


def kickoff_time_view(zone: str, minute: int | None) -> tuple[str, InlineKeyboardMarkup]:
    validate_zone(zone)
    lines = [
        tr("🕒 <b>Приоритетное время матча</b>"),
        tr("🌐 Часовой пояс: {zone}", zone=escape(timezone_label(zone))),
        tr("Выберите удобное местное время с шагом 30 минут."),
        tr("Время матча не выбрано: по умолчанию 18:00 UTC.")
        if minute is None
        else tr("Приоритетное время матча: {time}", time=f"{minute // 60:02}:{minute % 60:02}"),
        tr(
            "Назначенные матчи сохраняют время. "
            "Новое предпочтение учтётся в матчах за пределами ближайших 10 дней."
        ),
    ]
    buttons = [
        InlineKeyboardButton(
            text=("✓ " if chosen == minute else "") + f"{chosen // 60:02}:{chosen % 60:02}",
            callback_data=f"ko:minute:{chosen}",
        )
        for chosen in KICKOFF_MINUTES
    ]
    rows = [buttons[index : index + 4] for index in range(0, len(buttons), 4)]
    rows.append([InlineKeyboardButton(text=tr("← Настройки"), callback_data="ntf:open")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


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
