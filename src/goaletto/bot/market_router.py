"""Auction UI commits mutations before rendering/sending Telegram messages."""

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, Update

from goaletto.application.club_view import actor_for_callback
from goaletto.application.game_actions import PermissionDenied, actor_for
from goaletto.application.market import (
    get_auction,
    list_player,
    load_market,
    place_bid,
    withdraw_listing,
)
from goaletto.bot.market_views import (
    auction_view,
    listing_quote,
    market_view,
    parse_market,
    seller_view,
)
from goaletto.bot.responses import edit_response
from goaletto.infrastructure.database import Database
from goaletto.presentation.i18n import tr

router = Router(name="market")


@router.message(Command("market"))
async def market_command(message: Message, database: Database, event_update: Update) -> None:
    if message.from_user is None:
        return
    try:
        async with database.transaction() as session:
            actor = await actor_for(
                session, message.from_user.id, event_update.update_id, datetime.now(UTC)
            )
            text, keyboard = market_view(await load_market(session, actor))
    except (PermissionDenied, ValueError):
        await message.answer(tr("Рынок недоступен. Откройте /start."))
        return
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(
    F.data.startswith("mk:")
    | F.data.startswith("mb:")
    | F.data.startswith("ml:")
    | F.data.startswith("mw:")
)
async def market_callback(query: CallbackQuery, database: Database, event_update: Update) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    try:
        async with database.transaction() as session:
            actor = await actor_for_callback(
                session,
                telegram_id=query.from_user.id,
                callback_id=query.id,
                at=datetime.now(UTC),
                update_id=event_update.update_id,
            )
            action, key, owner, value = parse_market(query.data or "")
            if action in {"open", "p", "sell", "q"}:
                screen = await load_market(session, actor, value or 0 if action == "p" else 0)
                if owner and owner != screen.ownership_id:
                    raise PermissionDenied("Old market ownership")
                if action == "sell":
                    text, keyboard = seller_view(screen, value or 0)
                elif action == "q" and key is not None:
                    text, keyboard = listing_quote(screen, key)
                else:
                    text, keyboard = market_view(screen)
            else:
                if key is None:
                    raise ValueError("Missing auction reference")
                notice = None
                mutated = action in {"list", "bid", "withdraw"}
                if action == "list" and owner is not None and value is not None:
                    key = (await list_player(session, actor, owner, key, value)).id
                    notice = tr("Игрок выставлен на аукцион.")
                elif action == "bid" and owner is not None and value is not None:
                    result = await place_bid(session, actor, owner, key, value)
                    notice = (
                        tr("Ставка принята.")
                        if result.accepted
                        else tr("Аукцион отменён. Резерв освобождён.")
                    )
                elif action == "withdraw" and owner is not None:
                    await withdraw_listing(session, actor, owner, key)
                    notice = tr("Лот снят.")
                screen, item = await get_auction(session, actor, key, record=not mutated)
                text, keyboard = auction_view(screen, item, notice)
    except (PermissionDenied, ValueError):
        await edit_response(
            query.message,
            tr(
                "Действие не выполнено: окно закрыто, кнопка устарела, игрок недос"
                "тупен или не хватает денег/места. Последний сезон запрещает "
                "трансфер. Откройте /market."
            ),
        )
        return
    await edit_response(query.message, text, keyboard)
