"""Facility decision: seats, expected crowd, income, upkeep and a priced action."""

from html import escape
from uuid import UUID

from aiogram.types import InlineKeyboardButton as Button
from aiogram.types import InlineKeyboardMarkup as Keyboard

from goaletto.application.stadium import StadiumScreen
from goaletto.bot.club_cards import CLUB_CALLBACK
from goaletto.bot.finance_views import amount
from goaletto.bot.result_views import compact_id, expand_id
from goaletto.domain.finances import (
    STADIUM_CAPACITIES,
    STADIUM_PRICES,
    stadium_upkeep,
    ticket_income,
)
from goaletto.domain.names import latin_name
from goaletto.domain.stadium import EXPANSION_DAYS, match_stage, stadium_level
from goaletto.presentation.i18n import LabelMap, tr

COMPETITIONS = LabelMap(
    {
        "league": "Лига",
        "cup": "Кубок",
        "champions": "Кубок чемпионов",
        "friendly": "Товарищеский матч",
    }
)
STAGES = LabelMap(
    {
        "qualification": "Отбор",
        "round_of_64": "1/32 финала",
        "round_of_32": "1/16 финала",
        "round_of_16": "1/8 финала",
        "quarterfinal": "Четвертьфинал",
        "semifinal": "Полуфинал",
        "final": "Финал",
    }
)


def parse_stadium(data: str) -> tuple[str, UUID, int | None]:
    parts = data.split(":")
    if len(data.encode()) > 64 or parts[0] != "st" or len(parts) not in (3, 4):
        raise ValueError("Invalid stadium callback")
    if parts[1] == "o" and len(parts) == 3:
        return "o", expand_id(parts[2]), None
    if (
        parts[1] == "u"
        and len(parts) == 4
        and parts[3].isascii()
        and parts[3].isdigit()
        and len(parts[3]) <= 5
    ):
        capacity = int(parts[3])
        stadium_level(capacity)
        return "u", expand_id(parts[2]), capacity
    raise ValueError("Invalid stadium action")


def stadium_view(screen: StadiumScreen, notice: str | None = None) -> tuple[str, Keyboard]:
    level = stadium_level(screen.capacity)
    lines = [
        tr("🏟 <b>Стадион · {club}</b>", club=escape(latin_name(screen.club_name))),
        "",
        tr(
            "Уровень {level}/5 · мест: <b>{capacity}</b>",
            level=level,
            capacity=amount(screen.capacity),
        ),
        tr(
            "Болельщики: {supporters} · содержание: {upkeep}/период клуба",
            supporters=amount(screen.supporters)
            + (
                tr(" (+{count} недавно)", count=amount(screen.recent_gain))
                if screen.recent_gain
                else ""
            ),
            upkeep=amount(stadium_upkeep(screen.capacity)),
        ),
        tr(
            "Доступно: {available} · долг: {debt}",
            available=amount(screen.available),
            debt=amount(screen.debt),
        ),
    ]
    if notice:
        lines.extend(("", notice))
    if screen.next_home_at:
        lines.extend(
            ("", tr("Домашний матч: {date} UTC", date=screen.next_home_at.strftime("%d.%m %H:%M")))
        )
        stage = match_stage(screen.next_competition, screen.next_stage)
        lines.append(
            COMPETITIONS[screen.next_competition]
            + (" · " + STAGES[stage] if stage in STAGES else "")
        )
        crowd = screen.locked
        if crowd:
            lines.append(
                tr(
                    "На матч зафиксировано: {fans}/{capacity} зрителей",
                    fans=amount(crowd.home_fans),
                    capacity=amount(crowd.stadium_capacity),
                )
            )
            fans, cap = crowd.home_fans, crowd.stadium_capacity
        elif screen.projected:
            fans, cap = screen.projected.fans, screen.projected.capacity
            lines.append(
                tr("Ожидается: {fans}/{capacity} зрителей", fans=amount(fans), capacity=amount(cap))
            )
        else:
            raise ValueError("Home fixture lacks an attendance projection")
        lines.append(
            tr(
                "Билеты: +{income} · заполнение: {fill}%",
                income=amount(ticket_income(fans, cap)),
                fill=0 if cap == 0 else fans * 100 // cap,
            )
        )
    rows = []
    if screen.expansion:
        expansion = screen.expansion
        lines.extend(
            (
                "",
                tr("🚧 Расширение до {capacity} мест", capacity=amount(expansion.to_capacity)),
                tr(
                    "Оплачено: {cost} · готовность: {date} UTC",
                    cost=amount(expansion.cost),
                    date=expansion.due_at.strftime("%d.%m %H:%M"),
                ),
                tr("До завершения доступны текущие трибуны."),
            )
        )
    elif level < 5:
        cap, cost = STADIUM_CAPACITIES[level], STADIUM_PRICES[level]
        lines.extend(
            (
                "",
                tr(
                    "Следующий уровень: {capacity} мест · цена: {cost}",
                    capacity=amount(cap),
                    cost=amount(cost),
                ),
                tr(
                    "Срок: {days} дней · содержание: {upkeep}/период клуба",
                    days=EXPANSION_DAYS,
                    upkeep=amount(stadium_upkeep(cap)),
                ),
            )
        )
        if not screen.debt and screen.available >= cost:
            rows.append(
                [
                    Button(
                        text=tr("Расширить за {cost}", cost=amount(cost)),
                        callback_data=f"st:u:{compact_id(screen.ownership_id)}:{screen.capacity}",
                    )
                ]
            )
        else:
            lines.append(tr("Для расширения нужны свободные деньги и погашенный долг."))
    else:
        lines.extend(("", tr("Достигнут максимальный уровень стадиона.")))
    rows.append(
        [Button(text=tr("↻ Обновить"), callback_data=f"st:o:{compact_id(screen.ownership_id)}")]
    )
    rows.append(
        [
            Button(text=tr("📈 Финансы"), callback_data="fi:open"),
            Button(text=tr("🏠 Клуб"), callback_data=CLUB_CALLBACK),
        ]
    )
    return "\n".join(lines), Keyboard(inline_keyboard=rows)
