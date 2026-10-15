"""Search IANA city/region names without inferring a timezone from language."""

from datetime import datetime
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from goaletto.domain.kickoff import local_datetime, search_zones
from goaletto.presentation.i18n import tr


def zone_search_view(query: str, at: datetime) -> tuple[str, InlineKeyboardMarkup]:
    zones = (
        search_zones(query)
        if query
        else (
            "UTC",
            "Europe/Belgrade",
            "Europe/Moscow",
            "Europe/London",
            "Asia/Kolkata",
            "Asia/Kathmandu",
            "America/New_York",
        )
    )
    lines = [
        tr("🌐 Найдите город или регион: /timezone Belgrade"),
        tr("Нажмите на пояс, чтобы сохранить. Местное время сейчас:"),
    ]
    rows = []
    for zone in zones:
        lines.append(f"{escape(zone)} · {escape(local_datetime(at, zone))}")
        rows.append([InlineKeyboardButton(text=zone, callback_data=f"tz:{zone}")])
    if not zones:
        lines.append(tr("Пояс не найден. Используйте IANA-название, например Europe/Belgrade."))
    rows.append([InlineKeyboardButton(text=tr("← Настройки"), callback_data="ntf:open")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)
