"""Private search screens and bounded, ownership-bound callback data."""

from html import escape
from uuid import UUID

from aiogram.types import InlineKeyboardMarkup as Keyboard

from goaletto.application.scouting import Finding, ScoutScreen
from goaletto.bot.finance_views import amount
from goaletto.bot.market_views import button
from goaletto.bot.result_views import compact_id
from goaletto.domain.finances import SCOUT_SEARCH_DAYS, scout_mission_fee
from goaletto.domain.player_positions import ADJACENT
from goaletto.presentation.i18n import tr
from goaletto.presentation.person_names import person_name


def view(screen: ScoutScreen, notice: str | None = None) -> tuple[str, Keyboard]:
    own = compact_id(screen.ownership_id)
    lines = [tr("🔎 <b>Скаутский поиск</b>"), tr("Доступно: {amount}", amount=amount(screen.money))]
    rows = []
    if screen.scout:
        lines += [
            f"{escape(person_name(screen.scout))} · {screen.skill}/20",
            tr("Содержание: {wage}/4 дня", wage=amount(screen.cycle_rate)),
        ]
    else:
        lines.append(tr("Для нового поиска наймите скаута в /staff."))
    active = next((m for m in screen.missions if m.status == "active"), None)
    if active:
        lines.append(
            tr(
                "Поиск {position} · {days} дней · оплачен: {fee}",
                position=active.position,
                days=active.days,
                fee=amount(active.fee),
            )
        )
        lines.append(tr("Итоговый отчёт: {date} UTC", date=active.due_at.strftime("%d.%m %H:%M")))
        rows.append([button(tr("Отменить поиск"), f"ss:c:{own}:{compact_id(active.id)}")])
    elif screen.scout:
        rows.append([button(tr("Новый поиск"), f"ss:n:{own}")])
    for mission in screen.missions:
        if mission.status == "completed":
            rows.append(
                [
                    button(
                        tr(
                            "Отчёт: {position} · {days} дней",
                            position=mission.position,
                            days=mission.days,
                        ),
                        f"ss:r:{own}:{compact_id(mission.id)}",
                    )
                ]
            )
    if notice:
        lines.append(notice)
    lines.append(tr("Поиск оплачивается отдельно. Все кандидаты появляются в конце задания."))
    rows += [
        [button(tr("↻ Обновить"), "ss:open")],
        [button(tr("Персонал"), "cg:s:0"), button(tr("🏠 Клуб"), "vc:c")],
    ]
    return "\n".join(lines), Keyboard(inline_keyboard=rows)


def positions(owner: UUID) -> tuple[str, Keyboard]:
    own = compact_id(owner)
    return tr("<b>Кого ищем?</b> Выберите целевую позицию."), Keyboard(
        inline_keyboard=[
            [button(p, f"ss:p:{own}:{p}") for p in list(ADJACENT)[i : i + 3]]
            for i in range(0, len(ADJACENT), 3)
        ]
        + [[button(tr("Назад"), "ss:open")]]
    )


def durations(screen: ScoutScreen, position: str) -> tuple[str, Keyboard]:
    if screen.scout is None or position not in ADJACENT:
        raise ValueError("Scout unavailable")
    own, scout = compact_id(screen.ownership_id), compact_id(screen.scout.id)
    from goaletto.domain.market import encode_price

    rows = [
        [
            button(
                tr(
                    "{days} дней · {count} кандид. · {fee}",
                    days=d,
                    count=d // 4,
                    fee=amount(scout_mission_fee(screen.skill, d)),
                ),
                f"ss:q:{own}:{scout}:{position}:{d}:"
                + encode_price(scout_mission_fee(screen.skill, d)),
            )
        ]
        for d in SCOUT_SEARCH_DAYS
    ]
    rows.append([button(tr("Назад"), f"ss:n:{own}")])
    return tr(
        "<b>Срок поиска · {position}</b>\nВсе находки будут в одном итоговом отчёте.",
        position=position,
    ), Keyboard(inline_keyboard=rows)


def search_quote(
    owner: UUID, scout: UUID, position: str, days: int, fee: int, callback: str
) -> tuple[str, Keyboard]:
    return tr(
        "<b>Отправить скаута?</b>\nПозиция: {position}\nСрок: {days} дней\n"
        "Кандидатов в конце: {count}\nПоиск: {fee}\nПодписание игроков оплачивается отдельно. "
        "При отмене стоимость поиска не возвращается.",
        position=position,
        days=days,
        count=days // 4,
        fee=amount(fee),
    ), Keyboard(
        inline_keyboard=[
            [button(tr("Оплатить поиск"), callback.replace("ss:q:", "ss:b:", 1))],
            [button(tr("Назад"), f"ss:p:{compact_id(owner)}:{position}")],
        ]
    )


def report(screen: ScoutScreen, mission_id: UUID) -> tuple[str, Keyboard]:
    mission = next(
        (m for m in screen.missions if m.id == mission_id and m.status == "completed"), None
    )
    if mission is None or mission.expires_at is None:
        raise ValueError("Report unavailable")
    lines = [
        tr("🔎 <b>Итоговый список · {position}</b>", position=mission.position),
        tr("Доступен до: {date} UTC", date=mission.expires_at.strftime("%d.%m %H:%M")),
    ]
    rows = []
    for item in screen.findings:
        if item.offer.mission_id != mission.id:
            continue
        lines.append(
            f"{escape(person_name(item.person))} · {item.person.age} · "
            + "/".join((item.player.position, *item.player.alternative_positions))
            + f" · {amount(item.offer.fee)}"
        )
        rows.append(
            [
                button(
                    person_name(item.person),
                    f"ss:f:{compact_id(screen.ownership_id)}:{compact_id(item.person.id)}",
                )
            ]
        )
    if not rows:
        lines.append(tr("Доступных кандидатов в этом отчёте больше нет."))
    rows.append([button(tr("Назад"), "ss:open")])
    return "\n".join(lines), Keyboard(inline_keyboard=rows)


def finding_view(item: Finding, owner: UUID) -> tuple[str, Keyboard]:
    from goaletto.bot.club_cards import SKILL_LABELS
    from goaletto.domain.market import encode_price

    own, person = compact_id(owner), compact_id(item.person.id)
    lines = [
        f"<b>{escape(person_name(item.person))}</b> · {item.person.age} · "
        + "/".join((item.player.position, *item.player.alternative_positions))
    ]
    lines += [f"{tr(label)}: {getattr(item.player, key)}" for key, label in SKILL_LABELS[:8]]
    lines += [
        tr(
            "Оценка потенциала позиции: {low}–{high}/20",
            low=item.offer.potential_low,
            high=item.offer.potential_high,
        ),
        tr(
            "Подписание: {fee} · зарплата: {wage}/4 дня",
            fee=amount(item.offer.fee),
            wage=amount(item.offer.cycle_rate),
        ),
        tr("Подписание доступно в трансферное окно. Неподписанные находки не выходят на рынок."),
    ]
    return "\n".join(lines), Keyboard(
        inline_keyboard=[
            [
                button(
                    tr("Подписать за {fee}", fee=amount(item.offer.fee)),
                    f"ss:s:{own}:{person}:{encode_price(item.offer.fee)}",
                )
            ],
            [button(tr("Отказаться"), f"ss:d:{own}:{person}")],
            [button(tr("Назад"), f"ss:r:{own}:{compact_id(item.offer.mission_id)}")],
        ]
    )


def cancel_quote(owner: UUID, mission: UUID) -> tuple[str, Keyboard]:
    return tr(
        "Отменить поиск? Оплаченная стоимость не возвращается. "
        "Новое задание потребует новой оплаты и полного срока."
    ), Keyboard(
        inline_keyboard=[
            [button(tr("Отменить поиск"), f"ss:k:{compact_id(owner)}:{compact_id(mission)}")],
            [button(tr("Назад"), "ss:open")],
        ]
    )
