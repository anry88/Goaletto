"""Explicit review and confirmation before terminating a football contract."""

from datetime import UTC, datetime
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message, Update
from aiogram.types import InlineKeyboardButton as Button
from aiogram.types import InlineKeyboardMarkup as Keyboard

from goaletto.application.club_view import actor_for_callback
from goaletto.application.game_actions import PermissionDenied
from goaletto.application.player_release import confirm_release, quote_release
from goaletto.bot.responses import edit_response
from goaletto.bot.result_views import compact_id, expand_id
from goaletto.domain.names import latin_name
from goaletto.infrastructure.database import Database
from goaletto.presentation.i18n import tr

router = Router(name="player-release")


@router.callback_query(F.data.startswith("pr:"))
async def player_release_callback(
    query: CallbackQuery, database: Database, event_update: Update
) -> None:
    await query.answer()
    if not isinstance(query.message, Message):
        return
    try:
        data = query.data or ""
        parts = data.split(":")
        if len(data.encode()) > 64 or parts[0] != "pr":
            raise ValueError("Invalid release action")
        async with database.transaction() as session:
            actor = await actor_for_callback(
                session,
                telegram_id=query.from_user.id,
                callback_id=query.id,
                at=datetime.now(UTC),
                update_id=event_update.update_id,
            )
            if len(parts) == 4 and parts[1] == "q":
                row = await quote_release(session, actor, expand_id(parts[3]), expand_id(parts[2]))
                text = tr(
                    "<b>Расторгнуть контракт · {name}</b>\n\nКомпенсация: {amount:,} — одна"
                    " зарплата за 4-дневный игровой цикл. "
                    "Нужны свободные деньги с учётом резервов. Начисленная зарплата "
                    "сохраняется; новые начисления прекратятся с момента расторжения. "
                    "Игрок выйдет на общий рынок с обычной ценой подписания. Клуб денег "
                    "за его уход не получает.\n\nПодтверждение действует 15 минут.",
                    name=escape(latin_name(row.name)),
                    amount=row.compensation,
                )
                keyboard = Keyboard(
                    inline_keyboard=[
                        [
                            Button(
                                text=tr("Подтвердить расторжение"),
                                callback_data=f"pr:c:{compact_id(row.id)}",
                            )
                        ],
                        [
                            Button(
                                text=tr("Отмена"),
                                callback_data=f"vc:p:{compact_id(row.person_id)}:1",
                            )
                        ],
                    ]
                )
            elif len(parts) == 3 and parts[1] == "c":
                row = await confirm_release(session, actor, expand_id(parts[2]))
                text = tr(
                    "Контракт с {name} расторгнут. Компенсация: {amount:,}. Игрок вышел "
                    "на общий рынок.",
                    name=escape(latin_name(row.name)),
                    amount=row.compensation,
                )
                keyboard = Keyboard(
                    inline_keyboard=[
                        [Button(text=tr("👥 Состав"), callback_data="vc:s:1")],
                        [Button(text=tr("🏠 Главное меню"), callback_data="vc:menu")],
                    ]
                )
            else:
                raise ValueError("Invalid release action")
    except (PermissionDenied, ValueError):
        await edit_response(
            query.message,
            tr(
                "Расторжение недоступно: проверьте свободные деньги, состав (минимум "
                "16 игроков и один вратарь), открытый аукцион и текущий матч. "
                "Игрок с последним сезоном должен остаться в клубе. Если "
                "подтверждение устарело, откройте карточку заново."
            ),
        )
        return
    await edit_response(query.message, text, keyboard)
