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
from goaletto.presentation.person_names import person_name

_LABELS = {name: label for name, label in SKILL_LABELS}


def parse_academy(data: str) -> tuple[str, UUID, int | None, UUID | None]:
    parts = data.split(":")
    if len(data.encode()) > 64 or parts[0] != "ac":
        raise ValueError("Invalid academy callback")
    if len(parts) == 3 and parts[1] == "o":
        return "o", expand_id(parts[2]), None, None
    if len(parts) == 4 and parts[1] == "u" and parts[3] in {"1", "2", "3", "4"}:
        return "u", expand_id(parts[2]), int(parts[3]), None
    if len(parts) == 5 and parts[1] == "g" and parts[4] in {"1", "2", "3", "4", "5"}:
        return "g", expand_id(parts[2]), int(parts[4]), expand_id(parts[3])
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
        name=escape(person_name(view)),
        position=position,
        age=view.age,
    )
    if view.status == "signed":
        header = f"{header} · {tr('✓ подписан')}"
    hint = tr("Высокий потенциал") if max(view.caps.values()) >= 12 else tr("Есть запас для роста")
    return [
        header,
        tr("   Ключевые: {skills}", skills=_top_skills(view.skills)),
        hint,
    ]


def academy_view(screen: AcademyScreen, notice: str | None = None) -> tuple[str, Keyboard]:
    lines = [
        tr("⚽ <b>Академия · {club}</b>", club=escape(latin_name(screen.club_name))),
        "",
        tr(
            "Уровень: {level}/5 · содержание: {upkeep} за 4 дня",
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
    if not screen.intake_open:
        lines.append(tr("Выпуск академии доступен в межсезонье."))
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
        if (
            view.status == "available"
            and screen.intake_open
            and screen.signed < screen.sign_limit
            and screen.roster_size < ROSTER_LIMIT
        ):
            rows.append(
                [
                    Button(
                        text=tr(
                            "Подписать: {name}",
                            name=person_name(view),
                        ),
                        callback_data=f"ac:g:{compact_id(screen.ownership_id)}:{compact_id(screen.season_id)}:{view.slot}",
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
                        callback_data=f"ac:u:{compact_id(screen.ownership_id)}:{screen.level}",
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
