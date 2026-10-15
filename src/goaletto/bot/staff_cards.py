"""Staff portrait captions and navigation; monetary confirmation stays separate."""

from html import escape

from aiogram.types import InlineKeyboardMarkup as Keyboard

from goaletto.application.staff_cards import StaffCard
from goaletto.bot.candidate_views import QUALITY_LABELS, ROLES, staff_contribution
from goaletto.bot.finance_views import amount
from goaletto.bot.market_views import button
from goaletto.bot.result_views import compact_id
from goaletto.domain.market import encode_price
from goaletto.domain.names import latin_name
from goaletto.domain.staff_quality import quality_tier, staff_abilities
from goaletto.presentation.i18n import tr


def staff_card_view(card: StaffCard, page: int = 0) -> tuple[str, Keyboard]:
    person, terms = card.person, card.terms
    skill, wage = int(terms["skill"]), int(terms["wage"])
    lines = [
        f"<b>{escape(latin_name(person.name))}</b> · {person.age}",
        f"{ROLES[card.role]} · {QUALITY_LABELS[quality_tier(skill)]} · {skill}/20",
        tr("В штабе клуба") if card.employed else tr("Кандидат на должность"),
        tr("Зарплата: {wage}/4 дня", wage=amount(wage)),
    ]
    if not card.employed:
        lines.append(tr("Подписание: {fee}", fee=amount(int(terms["fee"]))))
    if terms["status"] == "last_season":
        lines.append(tr("⏳ Последний рабочий сезон"))
    if terms.get("loyal"):
        lines.append(
            tr(
                "Лояльность клубу: {normal} → {wage}/4 дня (−20%).",
                normal=amount(int(terms["normal_wage"])),
                wage=amount(wage),
            )
        )
    if card.career:
        lines.append(tr("Бывший футболист · {position}", position=card.former_position or "—"))
        if card.former_club:
            lines.append(tr("Родной клуб: {club}", club=escape(latin_name(card.former_club))))
        if not card.employed and card.career.status == "pending":
            lines.append(
                tr(
                    "Первое предложение до {date} UTC.",
                    date=card.career.expires_at.strftime("%d.%m.%Y %H:%M"),
                )
            )
    lines.extend(("", staff_contribution(card.role, skill)))
    if card.role == "head_coach":
        from goaletto.bot.lineup_views import TACTIC_LABELS

        abilities = staff_abilities(card.role, skill)
        lines.append(
            tr(
                "Тактики: {tactics}.",
                tactics=", ".join(TACTIC_LABELS[t] for t in abilities.tactics),
            )
        )
        lines.append(
            tr(
                "Смены за матч: тактика {t}, инструкции {i}, настрой {m}.",
                t=abilities.tactic_changes,
                i=abilities.instruction_changes,
                m=abilities.mentality_changes,
            )
        )
    rows = []
    suffix = f"{compact_id(person.id)}:{compact_id(card.ownership_id)}"
    if card.employed and card.role == "head_coach":
        lines.extend(("", tr("Главного тренера можно только заменить через найм другого.")))
    elif card.employed:
        rows.append([button(tr("Уволить"), f"cg:f:{suffix}:0")])
    else:
        label = tr("Заменить сотрудника") if card.replacing else tr("Подписать")
        rows.append([button(label, f"cg:q:{suffix}:{encode_price(int(terms['fee']))}")])
        if card.career and card.career.status == "pending":
            rows.append([button(tr("Отклонить"), f"cg:d:{suffix}:0")])
    rows.append([button(tr("Назад"), f"cg:s:{page}"), button(tr("🏠 Клуб"), "vc:c")])
    return "\n".join(lines), Keyboard(inline_keyboard=rows)
