"""Club home media in the current kit colors, without technical color codes."""

from aiogram.types import InlineKeyboardMarkup, Message

from goaletto.application.club_view import ClubCard
from goaletto.bot.card_images import CardImage, photo_response
from goaletto.bot.club_cards import club_view
from goaletto.bot.responses import edit_response
from goaletto.infrastructure.database import Database
from goaletto.portraits.club_kit import POLICY, render_club_kit


def kit_image(card: ClubCard) -> CardImage:
    return CardImage(
        f"club:{card.id}",
        POLICY,
        1,
        card.kit_primary,
        card.kit_secondary,
        card.kit_version,
        lambda: render_club_kit(card.kit_primary, card.kit_secondary),
        background="kit-paper-v1",
    )


async def show_club(
    message: Message,
    database: Database,
    card: ClubCard,
    *,
    edit: bool,
    view: tuple[str, InlineKeyboardMarkup] | None = None,
) -> None:
    text, keyboard = view or club_view(card)
    if await photo_response(message, database, text, keyboard, kit_image(card), edit=edit):
        return
    if edit:
        await edit_response(message, text, keyboard)
    else:
        await message.answer(text, reply_markup=keyboard)
