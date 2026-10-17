"""Telegram boundary: short observation transactions, no network inside them."""

from datetime import UTC, datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from goaletto.application.analytics import Reason, Surface
from goaletto.application.club_view import actor_for_callback
from goaletto.application.game_actions import PermissionDenied, actor_for
from goaletto.application.onboarding_measurement import rejection, response_accepted
from goaletto.infrastructure.database import Database
from goaletto.presentation.i18n import tr


def recovery_keyboard(*, lineup: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if lineup:
        rows.append([InlineKeyboardButton(text=tr("Открыть состав"), callback_data="ln:open")])
        rows.append([InlineKeyboardButton(text=tr("🏟 Клуб"), callback_data="vc:c")])
    else:
        rows.append([InlineKeyboardButton(text=tr("Выбрать клуб"), callback_data="en:o:1")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def observe(
    database: Database,
    telegram_id: int,
    update_id: int,
    surface: Surface,
    reason: Reason | None = None,
    *,
    offer: bool = False,
    callback_id: str | None = None,
) -> None:
    at = datetime.now(UTC)
    async with database.transaction() as session:
        try:
            actor = (
                await actor_for(session, telegram_id, update_id, at)
                if callback_id is None
                else await actor_for_callback(
                    session,
                    telegram_id=telegram_id,
                    callback_id=callback_id,
                    at=at,
                    update_id=update_id,
                )
            )
        except PermissionDenied:
            return  # Unknown users must first enter explicitly through /start.
        if reason is not None:
            await rejection(session, actor, surface, reason)
        else:
            await response_accepted(session, actor, at, offer=offer)
