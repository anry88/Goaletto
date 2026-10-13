"""Edit text screens; replace a photo card when returning to text navigation."""

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message


async def edit_response(
    message: Message, text: str, keyboard: InlineKeyboardMarkup | None = None
) -> None:
    if message.photo:
        # Telegram cannot remove media through editMessageMedia. Deliver the next
        # screen first, then remove the old card; its new callbacks survive restart.
        await message.answer(text, reply_markup=keyboard)
        try:
            await message.delete()
        except TelegramBadRequest:
            await message.edit_caption(caption="Карточка закрыта.", reply_markup=None)
        return
    try:
        await message.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest as error:
        if "message is not modified" not in error.message.lower():
            raise
