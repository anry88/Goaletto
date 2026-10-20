from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from goaletto.presentation.channels import channel_links
from goaletto.presentation.i18n import tr

router = Router(name="prototype")
MENU = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="👥 Состав", callback_data="preview:squad"),
            InlineKeyboardButton(text="⚽ Матч", callback_data="preview:match"),
        ],
        [
            InlineKeyboardButton(text="🏆 Лига", callback_data="preview:league"),
            InlineKeyboardButton(text="🏋️ Тренировки", callback_data="preview:training"),
        ],
        [
            InlineKeyboardButton(text="💰 Трансферы", callback_data="preview:market"),
            InlineKeyboardButton(text="📊 Финансы", callback_data="preview:finance"),
        ],
    ]
)


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        tr(
            "⚽ <b>Goaletto · Голетто</b>\nВаш клуб. Ваш состав. Ваш путь "
            "к чемпионству.\n\nЭто предварительный интерфейс: игровые "
            "функции ещё разрабатываются."
        )
        + "\n\n"
        + channel_links(),
        reply_markup=MENU,
    )


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(
        tr(
            "/start — главное меню\n/help — помощь\n\nИгра будет работать "
            "целиком в Telegram через сообщения и кнопки."
        )
        + "\n\n"
        + channel_links()
    )


@router.callback_query(lambda query: bool(query.data and query.data.startswith("preview:")))
async def preview(query: CallbackQuery) -> None:
    await query.answer(tr("Этот раздел пока в разработке."), show_alert=False)
