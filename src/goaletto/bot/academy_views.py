"""Academy decision screen: level, upkeep, season shortlist and priced actions."""

from html import escape
from uuid import UUID

from aiogram.types import InlineKeyboardButton as Button
from aiogram.types import InlineKeyboardMarkup as Keyboard

from goaletto.application.academy import ROSTER_LIMIT, AcademyScreen, CandidateView
from goaletto.bot.club_cards import CLUB_CALLBACK, SKILL_LABELS
from goaletto.bot.finance_views import amount
from goaletto.bot.result_views import compact_id, expand_id
from goaletto.domain.academy import academy_upkeep
from goaletto.domain.names import latin_name
from goaletto.presentation.i18n import tr

_LABELS = {name: label for name, label in SKILL_LABELS}


def parse_academy(data: str) -> tuple[str, UUID, int | None]:
    parts = data.split(":")
    if len(data.encode()) > 64 or parts[0] != "ac" or len(parts) not in (3, 4):
        raise ValueError("Invalid academy callback")
    if parts[1] in ("o", "u") and len(parts) == 3:
        return parts[1], expand_id(parts[2]), None
    if (
        parts[1] == "g"
        and len(parts) == 4
        and parts[3].isascii()
        and parts[3].isdigit()
        and len(parts[3]) <= 2
    ):
        return "g", expand_id(parts[2]), int(parts[3])
    raise ValueError("Invalid academy callback")


def _top_skills(skills: dict[str, int]) -> str:
    ranked = sorted(
        ((name, skills[name]) for name in _LABELS if name in skills),
        key=lambda item: (-item[1], item[0]),
    )[:3]
    return " · ".join(f"{tr(_LABELS[name])} {value}" for name, value in ranked)


def _candidate_lines(view: CandidateView) -> list[str]:
    position = view.position
    if view.alternatives:
        position = f"{position} ({'/'.join(view.alternatives)})"
    header = tr(
        "{slot}. <b>{name}</b> · {position} · {age} лет",
        slot=view.slot,
        name=escape(latin_name(view.name)),
        position=position,
        age=view.age,
    )
    if view.status == "signed":
        header = f"{header} · {tr('✓ подписан')}"
    low, high = min(view.caps.values()), max(view.caps.values())
    return [
        header,
        tr("   Ключевые: {skills}", skills=_top_skills(view.skills)),
        tr("   Потенциал: {low}–{high} (потолок навыков)", low=low, high=high),
    ]


def academy_view(screen: AcademyScreen, notice: str | None = None) -> tuple[str, Keyboard]:
    lines = [
        tr("⚽ <b>Академия · {club}</b>", club=escape(latin_name(screen.club_name))),
        "",
        tr(
            "Уровень: {level}/5 · содержание: {upkeep}/период клуба",
            level=screen.level,
            upkeep=amount(academy_upkeep(screen.level)),
        ),
        tr(
            "Доступно: {available} · долг: {arrears}",
            available=amount(screen.available),
            arrears=amount(screen.arrears),
        ),
        tr(
            "Состав: {roster}/{limit} · подписано в этом сезоне: {signed}/{cap}",
            roster=screen.roster_size,
            limit=ROSTER_LIMIT,
            signed=screen.signed,
            cap=screen.sign_limit,
        ),
    ]
    if notice:
        lines.extend(("", notice))
    if screen.next_upgrade_cost is not None:
        lines.append(
            tr(
                "Следующий уровень: {level} · стоимость: {cost}",
                level=screen.level + 1,
                cost=amount(screen.next_upgrade_cost),
            )
        )
    else:
        lines.append(tr("Достигнут максимальный уровень академии."))
    lines.extend(("", tr("<b>Кандидаты сезона (возраст 17)</b>")))
    for view in screen.candidates:
        lines.extend(_candidate_lines(view))
    lines.append("")
    lines.append(
        tr(
            "Набор фиксируется раз за сезон по текущему уровню. Подписанные юноши "
            "приходят с потенциалом уровня; лимит подписаний и размер состава указаны выше."
        )
    )
    rows = []
    for view in screen.candidates:
        if view.status == "available":
            rows.append(
                [
                    Button(
                        text=tr("Подписать: {name}", name=latin_name(view.name)),
                        callback_data=f"ac:g:{compact_id(screen.ownership_id)}:{view.slot}",
                    )
                ]
            )
    if screen.next_upgrade_cost is not None:
        if not screen.arrears and screen.available >= screen.next_upgrade_cost:
            rows.append(
                [
                    Button(
                        text=tr(
                            "Улучшить до {level} за {cost}",
                            level=screen.level + 1,
                            cost=amount(screen.next_upgrade_cost),
                        ),
                        callback_data=f"ac:u:{compact_id(screen.ownership_id)}",
                    )
                ]
            )
        else:
            lines.append(tr("Для улучшения нужны свободные деньги и погашенный долг."))
    rows.append(
        [Button(text=tr("↻ Обновить"), callback_data=f"ac:o:{compact_id(screen.ownership_id)}")]
    )
    rows.append(
        [
            Button(text=tr("📈 Финансы"), callback_data="fi:open"),
            Button(text=tr("🏠 Клуб"), callback_data=CLUB_CALLBACK),
        ]
    )
    return "\n".join(lines), Keyboard(inline_keyboard=rows)
