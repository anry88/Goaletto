"""Shared free-agent and staff hiring screens; prices are quoted before signing."""

from html import escape
from uuid import UUID

from aiogram.types import InlineKeyboardMarkup as Keyboard

from goaletto.application.candidates import CandidateItem, CandidateScreen
from goaletto.bot.finance_views import amount
from goaletto.bot.market_views import button
from goaletto.bot.result_views import compact_id, expand_id
from goaletto.domain.finances import player_quality
from goaletto.domain.market import decode_price, encode_price
from goaletto.domain.names import latin_name
from goaletto.domain.staff_quality import QUALITY_NAMES, quality_tier
from goaletto.infrastructure.models import StaffChange
from goaletto.presentation.i18n import LabelMap, tr
from goaletto.presentation.person_names import person_button_name, person_name

ROLES = LabelMap(
    {
        "head_coach": "Главный тренер",
        "goalkeeping_coach": "Тренер вратарей",
        "defence_coach": "Тренер защиты",
        "attack_coach": "Тренер атаки",
        "midfield_coach": "Тренер полузащиты",
        "wing_coach": "Тренер флангов",
        "fitness_coach": "Фитнес-тренер",
        "physiotherapist": "Физиотерапевт",
        "scout": "Скаут",
    }
)
QUALITY_LABELS = LabelMap(dict(enumerate(QUALITY_NAMES, 1)))
PAGE_SIZE = 4


def parse_candidate(data: str) -> tuple[str, UUID | None, UUID | None, int]:
    if not isinstance(data, str) or len(data.encode()) > 64:
        raise ValueError("Invalid candidate callback")
    p = data.split(":")
    if (
        len(p) == 3
        and p[0] == "cg"
        and p[1] in {"p", "s", "h"}
        and p[2].isascii()
        and p[2].isdigit()
        and len(p[2]) <= 3
    ):
        return p[1], None, None, int(p[2])
    if len(p) == 3 and p[:2] == ["cg", "x"]:
        return "x", expand_id(p[2]), None, 0
    if len(p) == 5 and p[0] == "cg" and p[1] in {"q", "b", "f", "d", "v"}:
        return p[1], expand_id(p[2]), expand_id(p[3]), decode_price(p[4])
    raise ValueError("Invalid candidate action")


def club_staff_list(screen: CandidateScreen, notice: str | None = None) -> tuple[str, Keyboard]:
    lines = [f"<b>{tr('Персонал клуба')} · {escape(latin_name(screen.club_name))}</b>"]
    rows = []
    for employee_id, (role, name, age, skill, status) in zip(
        screen.employee_ids, screen.employed, strict=True
    ):
        lines.extend(
            (
                "",
                f"{ROLES[role]} · {escape(latin_name(name))} · {age}",
                f"{QUALITY_LABELS[quality_tier(skill)]} · {skill}/20",
            )
        )
        if status == "last_season":
            lines.append(tr("⏳ Последний рабочий сезон"))
        rows.append(
            [
                button(
                    latin_name(name),
                    f"cg:v:{compact_id(employee_id)}:{compact_id(screen.ownership_id)}:0",
                )
            ]
        )
    if not screen.employed:
        lines.extend(("", tr("В клубе пока нет сотрудников.")))
    if not any(row[0] == "head_coach" for row in screen.employed):
        lines.append(
            tr("Без главного тренера: обычная тактика, 1 инструкция и 1 смена настроя за матч.")
        )
    if notice:
        lines.extend(("", notice))
    rows.extend(
        (
            [button(tr("🔎 Поиск персонала"), "cg:h:0")],
            [button(tr("🔎 Скаутские задания"), "ss:open")],
            [button(tr("↻ Обновить"), "cg:s:0")],
            [button(tr("🏠 Клуб"), "vc:c")],
        )
    )
    return "\n".join(lines), Keyboard(inline_keyboard=rows)


def candidate_list(
    screen: CandidateScreen, kind: str, page: int = 0, notice: str | None = None
) -> tuple[str, Keyboard]:
    if kind not in ("player", "staff") or type(page) is not int or page < 0:
        raise ValueError("Invalid candidate page")
    prefix = "p" if kind == "player" else "h"
    available = screen.items
    pages = max(1, (len(available) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages - 1)
    title = tr("Свободные футболисты") if kind == "player" else tr("Рынок персонала")
    lines = [
        f"<b>{title} · {escape(latin_name(screen.club_name))}</b>",
        tr("Доступно: {amount}", amount=amount(screen.available_money)),
    ]
    if kind == "staff":
        lines.append(tr("Кандидаты для найма в клуб."))
    if notice:
        lines.extend(("", notice))
    rows = []
    for item in available[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]:
        offer, person = item.offer, item.person
        role = (
            "/".join((offer.profile, *item.alternatives))
            if kind == "player"
            else ROLES[offer.profile]
        )
        ability = (
            tr(
                "Квалификация: {quality} · {skill}/20",
                quality=QUALITY_LABELS[quality_tier(item.skills[0])],
                skill=item.skills[0],
            )
            if kind == "staff"
            else tr(
                "Вратарь {gk} · защита {defence} · пас {passing} · фланг {winger}\n"
                "Атака {attack} · стандарты {set_pieces} · "
                "выносливость {stamina} · скорость {pace}",
                **dict(
                    zip(
                        (
                            "gk",
                            "defence",
                            "passing",
                            "winger",
                            "attack",
                            "set_pieces",
                            "stamina",
                            "pace",
                        ),
                        item.skills,
                        strict=True,
                    )
                ),
            )
        )
        if kind == "player":
            ability = (
                tr("Качество: {quality}/20", quality=player_quality(item.skills, offer.profile))
                + "\n"
                + ability
            )
        lines.extend(
            (
                "",
                f"<b>{escape(person_name(person))}</b> · {person.age} · {role}",
                ability,
                tr(
                    "Подписание: {fee} · зарплата: {wage}/4 дня",
                    fee=amount(offer.fee),
                    wage=amount(item.wage if item.wage is not None else offer.weekly_rate),
                ),
            )
        )
        if kind == "staff" and offer.profile == "head_coach":
            lines.append(coach_capabilities(item.skills[0]))
        if item.loyal:
            lines.append(
                tr(
                    "Лояльность клубу: обычная зарплата {normal}, ваша {wage}/4 дня (−20%).",
                    normal=amount(offer.weekly_rate),
                    wage=amount(item.wage or offer.weekly_rate),
                )
            )
        if item.private_until:
            lines.append(
                tr(
                    "Первое предложение клубу до {date} UTC.",
                    date=item.private_until.strftime("%d.%m.%Y %H:%M"),
                )
            )
            rows.append(
                [
                    button(
                        tr(
                            "Отказаться: {name}",
                            name=person_name(person),
                        ),
                        f"cg:d:{compact_id(person.id)}:{compact_id(screen.ownership_id)}:0",
                    )
                ]
            )
        action = "v" if kind == "staff" else "q"
        value = page if kind == "staff" else offer.fee
        rows.append(
            [
                button(
                    person_button_name(person) if kind == "player" else person_name(person),
                    f"cg:{action}:{compact_id(person.id)}:{compact_id(screen.ownership_id)}:{encode_price(value)}",
                )
            ]
        )
    if not available:
        message = (
            tr("Кандидатов на рынке персонала сейчас нет.")
            if kind == "staff"
            else tr("Свободных кандидатов сейчас нет. Посмотрите аукционы в /market.")
        )
        lines.extend(("", message))
    nav = []
    if page:
        nav.append(button("◀️", f"cg:{prefix}:{page - 1}"))
    if page + 1 < pages:
        nav.append(button("▶️", f"cg:{prefix}:{page + 1}"))
    if nav:
        rows.append(nav)
    if kind == "staff":
        rows.append([button(tr("👥 Персонал клуба"), "cg:s:0")])
    rows.extend(
        (
            [button(tr("↻ Обновить"), f"cg:{prefix}:{page}")],
            [button(tr("💰 Рынок"), "mk:open"), button(tr("🏠 Клуб"), "vc:c")],
        )
    )
    return "\n".join(lines), Keyboard(inline_keyboard=rows)


def candidate_quote(item: CandidateItem, ownership_id: UUID) -> tuple[str, Keyboard]:
    offer, person = item.offer, item.person
    role = (
        "/".join((offer.profile, *item.alternatives))
        if offer.kind == "player"
        else ROLES[offer.profile]
    )
    text = tr(
        "Подписать {name} · {role}?\nРазово: {fee}\nЗарплата: {wage}/4 дня",
        name=escape(person_name(person)),
        role=role,
        fee=amount(offer.fee),
        wage=amount(item.wage if item.wage is not None else offer.weekly_rate),
    )
    if offer.kind == "player":
        text += "\n" + tr(
            "Качество: {quality}/20", quality=player_quality(item.skills, offer.profile)
        )
    if offer.kind == "staff":
        text += "\n" + tr(
            "Квалификация: {quality} · {skill}/20",
            quality=QUALITY_LABELS[quality_tier(item.skills[0])],
            skill=item.skills[0],
        )
    if offer.kind == "staff" and offer.profile == "head_coach":
        text += "\n" + coach_capabilities(item.skills[0])
    prefix = "p" if offer.kind == "player" else "h"
    return text, Keyboard(
        inline_keyboard=[
            [
                button(
                    tr("Подписать за {fee}", fee=amount(offer.fee)),
                    f"cg:b:{compact_id(person.id)}:{compact_id(ownership_id)}:{encode_price(offer.fee)}",
                )
            ],
            [button(tr("Назад"), f"cg:{prefix}:0")],
        ]
    )


def coach_capabilities(skill: int) -> str:
    """Full capabilities for a new match, from the same rules as CoachSnapshot."""
    from goaletto.bot.lineup_views import TACTIC_LABELS
    from goaletto.domain.match_types import EnginePolicy
    from goaletto.domain.staff_quality import staff_abilities

    abilities = staff_abilities("head_coach", skill)
    policy = EnginePolicy()
    return "\n".join(
        (
            tr(
                "Тактики ({count}): {tactics}.",
                count=len(abilities.tactics),
                tactics=", ".join(TACTIC_LABELS[t] for t in abilities.tactics),
            ),
            tr(
                "Смены за матч: тактика {t}, инструкции {i}, настрой {m}.",
                t=abilities.tactic_changes,
                i=abilities.instruction_changes,
                m=abilities.mentality_changes,
            ),
            tr(
                "Замены: {count} · окон: {windows} + перерыв (у всех тренеров).",
                count=policy.max_substitutions,
                windows=policy.max_windows,
            ),
        )
    )


def staff_change_view(change: StaffChange) -> tuple[str, Keyboard]:
    from goaletto.domain.staff_quality import staff_abilities

    if change.role == "head_coach" and change.incoming_id is None:
        return tr("Главного тренера можно только заменить через найм другого."), Keyboard(
            inline_keyboard=[[button(tr("Назад"), "cg:s:0")]]
        )
    lines = [f"<b>{ROLES[change.role]}</b>"]
    for key, label in (("outgoing", tr("Уходит")), ("incoming", tr("Приходит"))):
        person = change.terms.get(key)
        if person is None:
            continue
        lines.append(
            tr(
                "{label}: {name} · {age} · {skill}/20 · {wage}/4 дня",
                label=label,
                name=escape(latin_name(person["name"])),
                age=person["age"],
                skill=person["skill"],
                wage=amount(person["wage"]),
            )
        )
        if person.get("loyal"):
            lines.append(
                tr(
                    "Лояльность клубу: обычная зарплата {normal}, ваша {wage}/4 дня (−20%).",
                    normal=amount(person["normal_wage"]),
                    wage=amount(person["wage"]),
                )
            )
        lines.append(staff_contribution(change.role, person["skill"]))
        if person["status"] == "last_season":
            lines.append(tr("⏳ Последний рабочий сезон"))
        if change.role == "head_coach":
            abilities = staff_abilities(change.role, person["skill"])
            from goaletto.bot.lineup_views import TACTIC_LABELS

            lines.append(
                tr(
                    "Тактики: {tactics}. Смены за матч: тактика {t}, инструкции {i}, настрой {m}.",
                    tactics=", ".join(TACTIC_LABELS[t] for t in abilities.tactics),
                    t=abilities.tactic_changes,
                    i=abilities.instruction_changes,
                    m=abilities.mentality_changes,
                )
            )
        elif change.role == "physiotherapist":
            lines.append(tr("Восстановление энергии: {skill}/20", skill=person["skill"]))
        elif change.role == "scout":
            lines.append(tr("Поиск и оценка потенциала: {skill}/20", skill=person["skill"]))
        else:
            lines.append(tr("Тренировочная квалификация: {skill}/20", skill=person["skill"]))
    incoming = change.terms.get("incoming")
    lines.append(
        tr(
            "Разово: {fee}. Штрафа за увольнение нет.",
            fee=amount(incoming["fee"] if incoming else 0),
        )
    )
    if change.outgoing_id:
        lines.append(
            tr(
                "Старая зарплата начисляется до момента увольнения. "
                "Новый контракт действует с подписания."
            )
        )
    if change.role == "scout" and change.outgoing_id:
        lines.append(
            tr(
                "Текущий поиск скаута прекратится без возврата оплаты. "
                "Готовые отчёты сохранятся до их срока."
            )
        )
    if change.role == "head_coach":
        lines.append(
            tr(
                "Возможности текущего матча уже зафиксированы. "
                "Новый штаб влияет на следующие матчи."
            )
        )
    return "\n".join(lines), Keyboard(
        inline_keyboard=[
            [button(tr("Подтвердить"), f"cg:x:{compact_id(change.id)}")],
            [button(tr("Назад"), "cg:h:0" if change.incoming_id else "cg:s:0")],
        ]
    )


def staff_contribution(role: str, skill: int) -> str:
    if role == "head_coach":
        return tr("Развивает лидерство на тренировках.")
    return {
        "goalkeeping_coach": tr("Открывает вратарскую подготовку. Качество определяет прирост."),
        "defence_coach": tr("Открывает оборонительную подготовку, включая опорников."),
        "attack_coach": tr("Атака, стандарты и развитие нападающих."),
        "midfield_coach": tr("Плеймейкинг и развитие полузащитников."),
        "wing_coach": tr("Фланговые тренировки и развитие крайних игроков."),
        "fitness_coach": tr("Открывает физподготовку: развитие выносливости и скорости."),
        "physiotherapist": tr("Ускоряет восстановление энергии и снижает риск травм на занятиях."),
        "scout": tr("Ищет молодых игроков в платных заданиях и оценивает их потенциал."),
    }[role]
