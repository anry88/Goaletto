"""Priced, scoped auction navigation. Financial mutations always quote an amount."""

from html import escape
from uuid import UUID

from aiogram.types import InlineKeyboardButton as Button
from aiogram.types import InlineKeyboardMarkup as Keyboard

from goaletto.application.market import MarketItem, MarketScreen
from goaletto.bot.club_cards import CLUB_CALLBACK, SKILL_LABELS
from goaletto.bot.finance_views import amount
from goaletto.bot.result_views import compact_id, expand_id
from goaletto.domain.finances import MAX_MONEY
from goaletto.domain.market import BID_STEP, decode_price, encode_price, minimum_bid
from goaletto.domain.names import latin_name
from goaletto.presentation.i18n import LabelMap, tr

STATES = LabelMap(
    {"open": "Открыт", "sold": "Продан", "expired": "Без ставок", "cancelled": "Отменён"}
)
REASONS = LabelMap(
    {
        "last_season": "объявлен последний сезон",
        "retired": "карьера завершена",
        "ownership_changed": "сменился владелец клуба",
        "roster_changed": "изменился состав",
        "calendar_changed": "сменился игровой сезон",
        "withdrawn": "снят продавцом",
        "operator": "отменён",
        "funds_changed": "платёж недоступен",
        "no_bids": "нет ставок",
        "sold": "сделка завершена",
    }
)


def button(text: str, data: str) -> Button:
    if len(data.encode()) > 64:
        raise ValueError("Market callback too long")
    return Button(text=text[:64], callback_data=data)


def parse_market(data: str) -> tuple[str, UUID | None, UUID | None, int | None]:
    if not isinstance(data, str) or len(data.encode()) > 64:
        raise ValueError("Invalid market callback")
    p = data.split(":")
    if p == ["mk", "open"]:
        return "open", None, None, 0
    if (
        len(p) == 3
        and p[0] == "mk"
        and p[1] in {"p", "sell"}
        and p[2].isascii()
        and p[2].isdigit()
        and len(p[2]) <= 6
    ):
        return p[1], None, None, int(p[2])
    if len(p) == 3 and p[:2] == ["mk", "a"]:
        return "a", expand_id(p[2]), None, None
    if len(p) == 4 and p[:2] == ["mk", "q"]:
        return "q", expand_id(p[2]), expand_id(p[3]), None
    if len(p) == 3 and p[0] == "mw":
        return "withdraw", expand_id(p[1]), expand_id(p[2]), None
    if len(p) == 4 and p[0] in {"mb", "ml"}:
        return (
            "bid" if p[0] == "mb" else "list",
            expand_id(p[1]),
            expand_id(p[2]),
            decode_price(p[3]),
        )
    raise ValueError("Invalid market action")


def market_view(screen: MarketScreen, notice: str | None = None) -> tuple[str, Keyboard]:
    window = screen.window
    opened = window.contains(screen.at)
    lines = [
        tr("💰 <b>Рынок · {club}</b>", club=escape(latin_name(screen.club_name))),
        tr(
            "Доступно: {available} · в резерве: {reserved}",
            available=amount(screen.cash - screen.reserved),
            reserved=amount(screen.reserved),
        ),
        tr(
            "Состав: {roster}/25 · ожидается: {incoming}",
            roster=screen.roster_size,
            incoming=screen.incoming,
        ),
        tr("Окно открыто до {date} UTC", date=window.closes_at.strftime("%d.%m %H:%M"))
        if opened
        else tr(
            "Окно: {start} — {end} UTC",
            start=window.opens_at.strftime("%d.%m %H:%M"),
            end=window.closes_at.strftime("%d.%m %H:%M"),
        ),
    ]
    if notice:
        lines.extend(("", notice))
    rows = []
    if not screen.items:
        lines.extend(("", tr("Открытых аукционов пока нет.")))
    for item in screen.items:
        auc = item.auction
        price = item.highest.amount if item.highest else auc.initial_price
        lines.extend(
            (
                "",
                f"{escape(latin_name(item.person.name))} · "
                f"{item.person.age} · "
                + "/".join((item.player.position, *item.player.alternative_positions)),
                tr(
                    "{club} · {price} · до {date} UTC",
                    club=escape(latin_name(item.seller_name)),
                    price=amount(price),
                    date=auc.ends_at.strftime("%d.%m %H:%M"),
                ),
            )
        )
        rows.append([button(latin_name(item.person.name), f"mk:a:{compact_id(auc.id)}")])
    rows.append([button(tr("Свободные футболисты"), "cg:p:0"), button(tr("Персонал"), "cg:s:0")])
    if opened:
        rows.append([button(tr("Продать игрока"), "mk:sell:0")])
    nav = []
    if screen.page:
        nav.append(button("◀️", f"mk:p:{screen.page - 1}"))
    if screen.page + 1 < screen.pages:
        nav.append(button("▶️", f"mk:p:{screen.page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([button(tr("↻ Обновить"), f"mk:p:{screen.page}")])
    rows.append([button(tr("📈 Финансы"), "fi:open"), button(tr("🏠 Клуб"), CLUB_CALLBACK)])
    return "\n".join(lines), Keyboard(inline_keyboard=rows)


def seller_view(screen: MarketScreen, page: int) -> tuple[str, Keyboard]:
    pages = max(1, (len(screen.sell_candidates) + 7) // 8)
    page = min(page, pages - 1)
    lines = [
        tr("<b>Выберите футболиста для продажи</b>"),
        tr("Аукцион: 48 часов · минимум 100 000."),
        tr(
            "Последний сезон запрещает трансфер. В клубе останутся минимум 16 "
            "игроков и один вратарь."
        ),
    ]
    rows = []
    for player, person in screen.sell_candidates[page * 8 : page * 8 + 8]:
        rows.append(
            [
                button(
                    f"{latin_name(person.name)} · {person.age} · "
                    + "/".join((player.position, *player.alternative_positions)),
                    f"mk:q:{compact_id(person.id)}:{compact_id(screen.ownership_id)}",
                )
            ]
        )
    if not screen.sell_candidates:
        lines.append(tr("Нет доступных игроков для нового аукциона."))
    nav = []
    if page:
        nav.append(button("◀️", f"mk:sell:{page - 1}"))
    if page + 1 < pages:
        nav.append(button("▶️", f"mk:sell:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([button(tr("← Рынок"), "mk:open")])
    return "\n".join(lines), Keyboard(inline_keyboard=rows)


def listing_quote(screen: MarketScreen, person_id: UUID) -> tuple[str, Keyboard]:
    person = next((n for p, n in screen.sell_candidates if p.person_id == person_id), None)
    if person is None:
        raise ValueError("Player not eligible to list")
    lines = [
        tr("<b>Стартовая цена · {player}</b>", player=escape(latin_name(person.name))),
        tr("Аукцион: 48 часов. При ставке в последние 2 часа срок продлевается на 30 минут."),
        tr("После первой ставки снять лот нельзя."),
    ]
    rows = [
        [
            button(
                amount(price),
                f"ml:{compact_id(person.id)}:{compact_id(screen.ownership_id)}:{encode_price(price)}",
            )
        ]
        for price in (100000, 150000, 250000, 500000, 1000000)
    ]
    rows.append([button(tr("← Рынок"), "mk:open")])
    return "\n".join(lines), Keyboard(inline_keyboard=rows)


def auction_view(
    screen: MarketScreen, item: MarketItem, notice: str | None = None
) -> tuple[str, Keyboard]:
    auc = item.auction
    bid = item.highest
    lines = [
        tr("💰 <b>Аукцион · {player}</b>", player=escape(latin_name(item.person.name))),
        tr(
            "Клуб: {club} · {age} лет · {position}",
            club=escape(latin_name(item.seller_name)),
            age=item.person.age,
            position="/".join((item.player.position, *item.player.alternative_positions)),
        ),
        tr(
            "Статус: {state}",
            state=tr("Расчёт ожидается")
            if auc.status == "open" and screen.at >= auc.ends_at
            else STATES[auc.status],
        ),
        tr(
            "Старт: {initial} · ставка: {bid}",
            initial=amount(auc.initial_price),
            bid=amount(bid.amount) if bid else tr("— нет"),
        ),
        tr("Завершение: {date} UTC", date=auc.ends_at.strftime("%d.%m %H:%M")),
        tr("Зарплата покупателя: {wage}/период клуба", wage=amount(auc.weekly_rate)),
    ]
    if auc.close_reason:
        lines.append(REASONS[auc.close_reason])
    if notice:
        lines.extend(("", notice))
    lines.append("")
    for key, label in SKILL_LABELS[:8]:
        lines.append(f"{tr(label)}: {getattr(item.player, key)}")
    if item.player.status == "last_season":
        lines.append(
            tr(
                "Это последний сезон футболиста. Он завершит его в своём клубе; тр"
                "ансфер недоступен."
            )
        )
    if item.player.injured_until and item.player.injured_until > screen.at:
        lines.append(
            tr(
                "🤕 Травмирован до {date} UTC",
                date=item.player.injured_until.strftime("%d.%m %H:%M"),
            )
        )
    rows = []
    if auc.status == "open" and screen.at < auc.ends_at:
        if auc.seller_club_id == screen.club_id:
            if bid is None:
                rows.append(
                    [
                        button(
                            tr("Снять без ставок"),
                            f"mw:{compact_id(auc.id)}:{compact_id(screen.ownership_id)}",
                        )
                    ]
                )
        elif (
            screen.window.contains(screen.at)
            and not screen.arrears
            and (bid is None or bid.amount <= MAX_MONEY - BID_STEP)
        ):
            price = minimum_bid(auc.initial_price, bid.amount if bid else None)
            available = (
                screen.cash
                - screen.reserved
                + (bid.amount if bid and bid.buyer_club_id == screen.club_id else 0)
            )
            if (
                price <= available
                and screen.roster_size
                + screen.incoming
                - (1 if bid and bid.buyer_club_id == screen.club_id else 0)
                < 25
            ):
                rows.append(
                    [
                        button(
                            tr("Ставка {price}", price=amount(price)),
                            f"mb:{compact_id(auc.id)}:{compact_id(screen.ownership_id)}:{encode_price(price)}",
                        )
                    ]
                )
                lines.append(tr("Сумма резервируется до завершения или перебитой ставки."))
            else:
                lines.append(tr("Для ставки нужны свободные деньги и место в составе."))
    elif auc.status == "open":
        lines.append(
            tr(
                "Аукцион закрыт для ставок. Расчёт ожидается; при активном матче п"
                "ереход дождётся финала."
            )
        )
    rows.append([button(tr("↻ Обновить"), f"mk:a:{compact_id(auc.id)}")])
    rows.append([button(tr("← Рынок"), "mk:open"), button(tr("📈 Финансы"), "fi:open")])
    return "\n".join(lines), Keyboard(inline_keyboard=rows)
