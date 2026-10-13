"""Native private-chat command menu, refreshed idempotently at bot startup."""

from aiogram import Bot
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
    ("market", "Аукционы и продажа игроков", "Auctions and player sales"),
    ("stadium", "Стадион и посещаемость", "Stadium and attendance"),
    ("finance", "Финансы клуба", "Club finances"),
    ("train", "Недельные тренировки", "Weekly training"),
    ("match", "Матч и ближайшая встреча", "Live match and next fixture"),
    ("table", "Таблица лиги", "League table"),
    ("history", "Результаты и отчёты матчей", "Match results and reports"),
    ("settings", "Язык и напоминания", "Language and reminders"),
    ("help", "Помощь по игре", "Game help"),
)


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
        )
    for chat_id, chosen in (saved_locales or {}).items():
        for code in ("", "ru", "en"):
            await bot.set_my_commands(
                command_menu(game_enabled=game_enabled, language=chosen),
                scope=BotCommandScopeChat(chat_id=chat_id),
                language_code=code,
            )
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
