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

ROLES = LabelMap(
    {
        "head_coach": "Главный тренер",
        "goalkeeping_coach": "Тренер вратарей",
        "defence_coach": "Тренер защиты",
        "attack_coach": "Тренер атаки",
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
        and p[1] in {"p", "s"}
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


def candidate_list(
    screen: CandidateScreen, kind: str, page: int = 0, notice: str | None = None
) -> tuple[str, Keyboard]:
    if kind not in ("player", "staff") or type(page) is not int or page < 0:
        raise ValueError("Invalid candidate page")
    prefix = "p" if kind == "player" else "s"
    busy_roles = {row[0] for row in screen.employed}
    available = screen.items
    pages = max(1, (len(available) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages - 1)
    title = tr("Свободные футболисты") if kind == "player" else tr("Персонал")
    lines = [
        f"<b>{title} · {escape(latin_name(screen.club_name))}</b>",
        tr("Доступно: {amount}", amount=amount(screen.available_money)),
    ]
    if kind == "staff" and screen.employed:
        lines.append(tr("В клубе:"))
        lines.extend(
            f"{ROLES[role]} · {escape(latin_name(name))} · {age} · "
            f"{QUALITY_LABELS[quality_tier(skill)]} · {skill}/20"
            + (" · " + tr("⏳ Последний рабочий сезон") if status == "last_season" else "")
            for role, name, age, skill, status in screen.employed
        )
    if notice:
        lines.extend(("", notice))
    rows = []
    if kind == "staff":
        for employee_id, employee in zip(screen.employee_ids, screen.employed, strict=True):
            rows.append(
                [
                    button(
                        latin_name(employee[1]),
                        f"cg:v:{compact_id(employee_id)}:{compact_id(screen.ownership_id)}:{encode_price(page)}",
                    )
                ]
            )
        if "head_coach" not in busy_roles:
            lines.append(
                tr("Без главного тренера: обычная тактика, 1 инструкция и 1 смена настроя за матч.")
            )
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
                f"<b>{escape(latin_name(person.name))}</b> · {person.age} · {role}",
                ability,
                tr(
                    "Подписание: {fee} · зарплата: {wage}/4 дня",
                    fee=amount(offer.fee),
                    wage=amount(item.wage if item.wage is not None else offer.weekly_rate),
                ),
            )
        )
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
                        tr("Отказаться: {name}", name=latin_name(person.name)),
                        f"cg:d:{compact_id(person.id)}:{compact_id(screen.ownership_id)}:0",
                    )
                ]
            )
        action = "v" if kind == "staff" else "q"
        value = page if kind == "staff" else offer.fee
        rows.append(
            [
                button(
                    latin_name(person.name),
                    f"cg:{action}:{compact_id(person.id)}:{compact_id(screen.ownership_id)}:{encode_price(value)}",
                )
            ]
        )
    if not available:
        message = (
            tr("Все профессии в клубе заняты.")
            if kind == "staff" and len(busy_roles) == 7
            else tr("Свободных сотрудников на доступные профессии сейчас нет.")
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
        rows.append([button(tr("🔎 Скаутские задания"), "ss:open")])
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
        name=escape(latin_name(person.name)),
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
    prefix = "p" if offer.kind == "player" else "s"
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
            [button(tr("Назад"), "cg:s:0")],
        ]
    )


def staff_contribution(role: str, skill: int) -> str:
    if role == "head_coach":
        return tr(
            "Общая подготовка, помощь морали до {target}/100 "
            "и постепенное развитие лидерства на занятиях.",
            target=50 + skill,
        )
    return {
        "goalkeeping_coach": tr("Открывает вратарскую подготовку. Качество определяет прирост."),
        "defence_coach": tr("Открывает оборонительную подготовку, включая опорников."),
        "attack_coach": tr("Открывает атаку, плеймейкинг, фланги и стандарты."),
        "fitness_coach": tr("Открывает физподготовку: развитие выносливости и скорости."),
        "physiotherapist": tr("Ускоряет восстановление энергии и снижает риск травм на занятиях."),
        "scout": tr("Ищет молодых игроков в платных заданиях и оценивает их потенциал."),
    }[role]
