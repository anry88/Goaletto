"""Shared position and status wording for the squad and lineup screens."""

from goaletto.presentation.i18n import tr


def position_warning(slot: str, declared: tuple[str, ...]) -> str:
    if slot in declared:
        return ""
    return f" ⚠️ {'/'.join(declared)}"


def squad_legend() -> tuple[str, str]:
    return (
        tr("⚠️ — не на своей позиции · 🔶 — последний сезон · 🤕 — травма"),
        tr("⛔ — пропуск матча · 🟨1/2 — жёлтая карточка"),
    )
