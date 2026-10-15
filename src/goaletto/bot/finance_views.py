"""Club cash, weekly forecast and bounded ledger navigation."""

from html import escape
from uuid import UUID

from aiogram.types import InlineKeyboardButton as Button
from aiogram.types import InlineKeyboardMarkup as Keyboard

from goaletto.application.finances import FinanceScreen
from goaletto.bot.club_cards import CLUB_CALLBACK
from goaletto.bot.result_views import compact_id, expand_id
from goaletto.domain.names import latin_name
from goaletto.presentation.i18n import LabelMap, tr

KINDS = LabelMap(
    {
        "opening": "Начальный баланс",
        "grant": "Стартовый капитал",
        "tickets": "Билеты",
        "sponsor": "Спонсор",
        "merchandise": "Мерч",
        "player_salary": "Зарплаты игроков",
        "staff_salary": "Зарплаты штаба",
        "stadium_upkeep": "Содержание стадиона",
        "prize": "Призовые",
        "reserve": "Резерв ставки",
        "release": "Возврат резерва",
        "transfer_in": "Продажа игрока",
        "transfer_out": "Покупка игрока",
        "arrears_paid": "Погашение задолженности",
        "stadium_upgrade": "Расширение стадиона",
    }
)


def amount(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def parse_page(data: str) -> tuple[UUID, int]:
    parts = data.split(":")
    if (
        len(parts) != 3
        or parts[0] != "fi"
        or len(data.encode()) > 64
        or not parts[2].isascii()
        or not parts[2].isdigit()
        or len(parts[2]) > 8
    ):
        raise ValueError("Invalid finance navigation")
    return expand_id(parts[1]), int(parts[2])


def finance_view(screen: FinanceScreen) -> tuple[str, Keyboard]:
    forecast = (
        screen.sponsor
        + screen.merchandise
        - screen.weekly_player_wages
        - screen.weekly_staff_wages
        - screen.upkeep
    )
    lines = [
        tr("📈 <b>Финансы · {club}</b>", club=escape(latin_name(screen.club_name))),
        "",
        tr("Баланс: <b>{cash}</b>", cash=amount(screen.balance)),
        tr(
            "В резерве: {reserved} · доступно: <b>{available}</b>",
            reserved=amount(screen.reserved),
            available=amount(screen.balance - screen.reserved),
        ),
        tr("Задолженность: {arrears}", arrears=amount(screen.arrears)),
        "",
        tr("<b>Прогноз на 4 дня</b>"),
        tr(
            "Спонсор: +{sponsor} · мерч: +{merchandise}",
            sponsor=amount(screen.sponsor),
            merchandise=amount(screen.merchandise),
        ),
        tr(
            "Игроки: −{players} · штаб: −{staff}",
            players=amount(screen.weekly_player_wages),
            staff=amount(screen.weekly_staff_wages),
        ),
        tr(
            "Стадион: −{upkeep} · итог: {net}",
            upkeep=amount(screen.upkeep),
            net=("+" if forecast >= 0 else "−") + amount(abs(forecast)),
        ),
        tr("Билеты и призовые начисляются отдельно."),
    ]
    if screen.due_at:
        lines.append(tr("Расчёт: {date} UTC", date=screen.due_at.strftime("%d.%m %H:%M")))
    if screen.arrears:
        lines.append(tr("Поступления погасят долг. Покупки недоступны до погашения."))
    if screen.last_cycle:
        lines.extend(
            (
                "",
                tr(
                    "Последний расчёт: {date} UTC",
                    date=screen.last_cycle.week_start.strftime("%d.%m"),
                ),
            )
        )
        for kind, value in screen.last_cycle.snapshot["amounts"].items():
            if value:
                lines.append(f"{KINDS[kind]}: {amount(value)}")
    lines.extend(
        ("", tr("<b>Операции · {page}/{pages}</b>", page=screen.page + 1, pages=screen.pages))
    )
    for entry in screen.entries:
        changes = []
        if entry.cash_delta:
            changes.append(("+" if entry.cash_delta > 0 else "−") + amount(abs(entry.cash_delta)))
        if entry.reserved_delta:
            changes.append(
                tr("резерв {change}", change=f"{entry.reserved_delta:+,}".replace(",", " "))
            )
        if entry.arrears_delta:
            changes.append(
                tr("долг {change}", change=f"{entry.arrears_delta:+,}".replace(",", " "))
            )
        lines.append(
            f"{entry.occurred_at:%d.%m} · {KINDS[entry.kind]} · {'; '.join(changes) or '0'}"
        )
    pages = []
    for label, page in (("◀️", screen.page - 1), ("▶️", screen.page + 1)):
        if 0 <= page < screen.pages:
            pages.append(
                Button(text=label, callback_data=f"fi:{compact_id(screen.ownership_id)}:{page}")
            )
    rows = [pages] if pages else []
    rows.append(
        [
            Button(
                text=tr("↻ Обновить"),
                callback_data=f"fi:{compact_id(screen.ownership_id)}:{screen.page}",
            )
        ]
    )
    rows.append([Button(text=tr("🏠 Клуб"), callback_data=CLUB_CALLBACK)])
    return "\n".join(lines), Keyboard(inline_keyboard=rows)
