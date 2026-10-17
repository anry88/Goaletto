from typing import Any

from goaletto.bot.commands import command_menu, configure_commands


async def test_private_menu_refreshes_all_languages_and_game_mode() -> None:
    class Bot:
        def __init__(self) -> None:
            self.menus: list[Any] = []
            self.buttons: list[Any] = []

        async def set_my_commands(self, commands: Any, **kwargs: Any) -> None:
            self.menus.append((commands, kwargs))

        async def set_chat_menu_button(self, **kwargs: Any) -> None:
            self.buttons.append(kwargs)

    bot = Bot()
    await configure_commands(bot, game_enabled=True)  # type: ignore[arg-type]
    expected = [
        "start",
        "club",
        "squad",
        "lineup",
        "freeagents",
        "scout",
        "staff",
        "market",
        "stadium",
        "academy",
        "finance",
        "train",
        "calendar",
        "match",
        "friendly",
        "table",
        "history",
        "feedback",
        "settings",
        "help",
    ]
    assert len(bot.menus) == 3
    for commands, kwargs in bot.menus:
        assert [c.command for c in commands] == expected
        assert kwargs["scope"].type == "all_private_chats"
    assert [kwargs["language_code"] for _, kwargs in bot.menus] == ["", "ru", "en"]
    assert bot.buttons[0]["menu_button"].type == "commands"
    await configure_commands(bot, game_enabled=False)  # type: ignore[arg-type]
    for commands, _ in bot.menus[3:]:
        assert [c.command for c in commands] == ["start", "help"]
    assert command_menu(game_enabled=True, language="en")[1].description == "My club"
