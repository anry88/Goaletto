"""Friendly challenge screens: inbox, date and opponent pickers."""

from datetime import date
from html import escape
from uuid import UUID

from aiogram.types import InlineKeyboardButton as Button
from aiogram.types import InlineKeyboardMarkup as Keyboard

from goaletto.application.friendly import FriendlyOpponent, FriendlyScreen
from goaletto.bot.result_views import compact_id, expand_id
from goaletto.domain.names import latin_name
from goaletto.presentation.i18n import LabelMap, tr

STATUS_LINES = LabelMap(
    {
        "accepted": "✅ Принято",
        "declined": "❌ Отклонено",
        "cancelled": "🚫 Отозвано",
        "expired": "⌛ Истекло",
    }
)


def friendly_view(screen: FriendlyScreen, notice: str | None = None) -> tuple[str, Keyboard]:
    lines = [tr("🤝 <b>Товарищеские матчи · {club}</b>", club=escape(latin_name(screen.club_name)))]
    if notice:
        lines.append(escape(notice))
    lines.append(tr("Вызывайте других менеджеров на игру в свободный день."))
    lines.append("")
    rows: list[list[Button]] = []
    if screen.incoming:
        lines.append(tr("<b>📥 Вас вызывают:</b>"))
        for card in screen.incoming:
            lines.append(
                tr(
                    "{home} — {day} UTC. Ответьте до конца дня.",
                    home=escape(latin_name(card.home_name)),
                    day=card.day.strftime("%d.%m"),
                )
            )
            rows.append(
                [
                    Button(text=tr("✅ Принять"), callback_data=f"fr:a:{compact_id(card.id)}"),
                    Button(text=tr("❌ Отклонить"), callback_data=f"fr:r:{compact_id(card.id)}"),
                ]
            )
        lines.append("")
    if screen.outgoing:
        lines.append(tr("<b>📤 Ваши вызовы:</b>"))
        for card in screen.outgoing:
            lines.append(
                tr(
                    "{away} — {day} UTC. Ожидает ответа.",
                    away=escape(latin_name(card.away_name)),
                    day=card.day.strftime("%d.%m"),
                )
            )
            rows.append(
                [Button(text=tr("🚫 Отозвать"), callback_data=f"fr:x:{compact_id(card.id)}")]
            )
        lines.append("")
    if not screen.incoming and not screen.outgoing:
        lines.append(tr("Вызовов нет."))
        lines.append("")
    if screen.recent:
        lines.append(tr("<b>Недавние:</b>"))
        for card in screen.recent:
            if card.status in STATUS_LINES:
                state = STATUS_LINES[card.status]
            else:
                state = escape(card.status)
            lines.append(
                tr(
                    "{home} — {away} · {day} UTC: {state}",
                    home=escape(latin_name(card.home_name)),
                    away=escape(latin_name(card.away_name)),
                    day=card.day.strftime("%d.%m"),
                    state=state,
                )
            )
        lines.append("")
    rows.append([Button(text=tr("⚽ Вызвать на матч"), callback_data="fr:c")])
    rows.append(
        [
            Button(text=tr("🗓 Расписание"), callback_data="ca:open"),
            Button(text=tr("🏠 Главное меню"), callback_data="vc:menu"),
        ]
    )
    return "\n".join(lines), Keyboard(inline_keyboard=rows)


def challenge_dates_view(days: tuple[date, ...]) -> tuple[str, Keyboard]:
    lines = [tr("<b>Свободный день игры (UTC):</b>")]
    rows: list[list[Button]] = []
    for day in days:
        rows.append([Button(text=day.strftime("%d.%m"), callback_data=f"fr:d:{day.isoformat()}")])
    if not days:
        lines.append(tr("Свободных дней нет: все даты заняты матчами или тренировками."))
    rows.append([Button(text=tr("← К вызовам"), callback_data="fr:open")])
    return "\n".join(lines), Keyboard(inline_keyboard=rows)


def challenge_opponents_view(
    day: date, opponents: tuple[FriendlyOpponent, ...]
) -> tuple[str, Keyboard]:
    lines = [
        tr("<b>Соперник на {day} UTC:</b>", day=day.strftime("%d.%m")),
        tr("👤 — менеджер, нужен его ответ. 🤖 — бот, матч сразу в расписание."),
    ]
    rows: list[list[Button]] = []
    for opponent in opponents:
        name = latin_name(opponent.name)[:50]
        if opponent.busy:
            lines.append(tr("⛔ {name} — занят в этот день.", name=escape(name)))
            continue
        marker = tr("👤 ") if opponent.owned else tr("🤖 ")
        rows.append(
            [
                Button(
                    text=f"{marker}{name}",
                    callback_data=f"fr:o:{day.isoformat()}:{opponent.club_id}",
                )
            ]
        )
    if not rows:
        lines.append(tr("Все клубы заняты в этот день. Выберите другую дату."))
    rows.append([Button(text=tr("← К датам"), callback_data="fr:c")])
    return "\n".join(lines), Keyboard(inline_keyboard=rows)


def parse_day(data: str) -> date:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "fr" or parts[1] != "d" or len(data.encode()) > 64:
        raise ValueError("Invalid friendly date")
    year, month, day = (int(item) for item in parts[2].split("-"))
    return date(year, month, day)


def parse_opponent(data: str) -> tuple[date, UUID]:
    parts = data.split(":")
    if len(parts) != 4 or parts[0] != "fr" or parts[1] != "o" or len(data.encode()) > 64:
        raise ValueError("Invalid friendly opponent")
    year, month, day = (int(item) for item in parts[2].split("-"))
    return date(year, month, day), UUID(parts[3])


def parse_invite(data: str, action: str) -> UUID:
    parts = data.split(":")
    if (
        len(parts) != 3
        or parts[0] != "fr"
        or parts[1] != action
        or len(data.encode()) > 64
        or not parts[2].isascii()
    ):
        raise ValueError("Invalid friendly invitation")
    return expand_id(parts[2])
