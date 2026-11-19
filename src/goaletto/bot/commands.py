"""Native private-chat command menu, refreshed idempotently at bot startup."""

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    MenuButtonCommands,
)

COMMANDS = (
    ("start", "Главное меню и получение клуба", "Main menu and club registration"),
    ("club", "Мой клуб", "My club"),
    ("squad", "Состав и карточки игроков", "Squad and player cards"),
    ("lineup", "Расстановка и тактика", "Lineup and tactics"),
    ("freeagents", "Свободные футболисты", "Free agents"),
    ("scout", "Скаутские задания и находки", "Scouting searches and findings"),
    ("staff", "Персонал и найм", "Staff and hiring"),
    ("market", "Аукционы и продажа игроков", "Auctions and player sales"),
    ("stadium", "Стадион и посещаемость", "Stadium and attendance"),
    ("academy", "Академия и выпуск юношей", "Academy and youth promotion"),
    ("country", "Страна клуба для академии", "Club country for academy graduates"),
    ("finance", "Финансы клуба", "Club finances"),
    ("train", "Тренировки", "Training"),
    ("calendar", "Расписание ближайших матчей", "Upcoming fixtures"),
    ("match", "Матч и ближайшая встреча", "Live match and next fixture"),
    ("friendly", "Товарищеские матчи и вызовы", "Friendly matches and challenges"),
    ("table", "Таблица лиги", "League table"),
    ("history", "Результаты и отчёты матчей", "Match results and reports"),
    ("feedback", "Оставить отзыв", "Leave feedback"),
    ("settings", "Настройки клуба, язык и время", "Club settings, language and time"),
    ("paysupport", "Покупки Stars и возвраты", "Stars purchases and refunds"),
    ("help", "Помощь по игре", "Game help"),
)
MENU_REQUEST_TIMEOUT = 12


def command_menu(*, game_enabled: bool, language: str = "ru") -> list[BotCommand]:
    selected = COMMANDS if game_enabled else tuple(c for c in COMMANDS if c[0] in ("start", "help"))
    return [
        BotCommand(command=c[0], description=c[2] if language == "en" else c[1]) for c in selected
    ]


async def configure_commands(
    bot: Bot, *, game_enabled: bool, saved_locales: dict[int, str] | None = None
) -> None:
    scope = BotCommandScopeAllPrivateChats()
    for language in ("", "ru", "en"):
        await bot.set_my_commands(
            command_menu(game_enabled=game_enabled, language=language),
            scope=scope,
            language_code=language,
            request_timeout=MENU_REQUEST_TIMEOUT,
        )
    for chat_id, chosen in (saved_locales or {}).items():
        for code in ("", "ru", "en"):
            try:
                await bot.set_my_commands(
                    command_menu(game_enabled=game_enabled, language=chosen),
                    scope=BotCommandScopeChat(chat_id=chat_id),
                    language_code=code,
                    request_timeout=MENU_REQUEST_TIMEOUT,
                )
            except TelegramForbiddenError:
                break
    await bot.set_chat_menu_button(
        menu_button=MenuButtonCommands(), request_timeout=MENU_REQUEST_TIMEOUT
    )
