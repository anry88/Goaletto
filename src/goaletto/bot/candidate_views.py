"""Shared free-agent and staff hiring screens; prices are quoted before signing."""

from html import escape
from uuid import UUID

from aiogram.types import InlineKeyboardMarkup as Keyboard

from goaletto.application.candidates import CandidateItem, CandidateScreen
from goaletto.bot.finance_views import amount
from goaletto.bot.market_views import button
from goaletto.bot.result_views import compact_id, expand_id
from goaletto.domain.market import decode_price, encode_price
from goaletto.domain.names import latin_name
from goaletto.domain.staff_quality import QUALITY_NAMES, quality_tier
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
    if len(p) == 5 and p[:2] in (["cg", "q"], ["cg", "b"]):
        return p[1], expand_id(p[2]), expand_id(p[3]), decode_price(p[4])
    raise ValueError("Invalid candidate action")


def candidate_list(
    screen: CandidateScreen, kind: str, page: int = 0, notice: str | None = None
) -> tuple[str, Keyboard]:
    if kind not in ("player", "staff") or type(page) is not int or page < 0:
        raise ValueError("Invalid candidate page")
    prefix = "p" if kind == "player" else "s"
    busy_roles = {row[0] for row in screen.employed}
    available = tuple(
        i for i in screen.items if kind == "player" or i.offer.profile not in busy_roles
    )
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
    for item in available[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]:
        offer, person = item.offer, item.person
        role = offer.profile if kind == "player" else ROLES[offer.profile]
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
        lines.extend(
            (
                "",
                f"<b>{escape(latin_name(person.name))}</b> · {person.age} · {role}",
                ability,
                tr(
                    "Подписание: {fee} · зарплата: {wage}/период клуба",
                    fee=amount(offer.fee),
                    wage=amount(offer.weekly_rate),
                ),
            )
        )
        rows.append(
            [
                button(
                    latin_name(person.name),
                    f"cg:q:{compact_id(person.id)}:{compact_id(screen.ownership_id)}:{encode_price(offer.fee)}",
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
    rows.extend(
        (
            [button(tr("↻ Обновить"), f"cg:{prefix}:{page}")],
            [button(tr("💰 Рынок"), "mk:open"), button(tr("🏠 Клуб"), "vc:c")],
        )
    )
    return "\n".join(lines), Keyboard(inline_keyboard=rows)


def candidate_quote(item: CandidateItem, ownership_id: UUID) -> tuple[str, Keyboard]:
    offer, person = item.offer, item.person
    role = offer.profile if offer.kind == "player" else ROLES[offer.profile]
    text = tr(
        "Подписать {name} · {role}?\nРазово: {fee}\nЗарплата: {wage}/период клуба",
        name=escape(latin_name(person.name)),
        role=role,
        fee=amount(offer.fee),
        wage=amount(offer.weekly_rate),
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
