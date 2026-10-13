from datetime import UTC, datetime
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage, EditMessageText
from aiogram.types import Chat, InlineKeyboardMarkup, Message, PhotoSize

from goaletto.bot.responses import edit_response


async def test_photo_to_text_delivers_next_screen_then_removes_old_card() -> None:
    calls: list[Any] = []
    message = Message(
        message_id=7,
        date=datetime.now(UTC),
        chat=Chat(id=1, type="private"),
        photo=[PhotoSize(file_id="face", file_unique_id="face", width=256, height=256)],
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[])

    async def answer(text: str, **kwargs: Any) -> None:
        calls.append(("answer", text, kwargs["reply_markup"]))

    async def delete() -> None:
        calls.append(("delete", 7))

    object.__setattr__(message, "answer", answer)
    object.__setattr__(message, "delete", delete)
    await edit_response(message, "Состав", keyboard)
    assert calls == [("answer", "Состав", keyboard), ("delete", 7)]

    async def denied_delete() -> None:
        raise TelegramBadRequest(DeleteMessage(chat_id=1, message_id=7), "cannot delete")

    async def edit_caption(**kwargs: Any) -> None:
        calls.append(("closed", kwargs))

    object.__setattr__(message, "delete", denied_delete)
    object.__setattr__(message, "edit_caption", edit_caption)
    await edit_response(message, "Главное меню", keyboard)
    assert calls[-1] == ("closed", {"caption": "Карточка закрыта.", "reply_markup": None})


async def test_repeated_text_edit_is_harmless() -> None:
    message = Message(message_id=7, date=datetime.now(UTC), chat=Chat(id=1, type="private"))

    async def edit_text(text: str, **kwargs: Any) -> None:
        raise TelegramBadRequest(
            EditMessageText(chat_id=1, message_id=7, text=text), "message is not modified"
        )

    object.__setattr__(message, "edit_text", edit_text)
    await edit_response(message, "Состав")
