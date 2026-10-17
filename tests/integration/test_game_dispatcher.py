"""Real router injection, ownership and commit boundaries; no Telegram network."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    EditMessageCaption,
    EditMessageMedia,
    EditMessageText,
    SendMessage,
    SendPhoto,
    SetMyCommands,
)
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Update, User
from sqlalchemy import select

from goaletto.application.world import initialize_world
from goaletto.bot.main import build_dispatcher
from goaletto.domain.world import WorldOptions
from goaletto.infrastructure.models import AcceptedCommand, ClubLineup, Manager, OwnershipPeriod

pytestmark = pytest.mark.integration


class Source:
    def __init__(self, sessions):
        self.sessions = sessions
        self.depth = 0

    @asynccontextmanager
    async def transaction(self):
        assert self.depth == 0
        self.depth += 1
        try:
            async with self.sessions.begin() as session:
                yield session
        finally:
            self.depth -= 1


class Telegram(BaseSession):
    def __init__(self, source):
        super().__init__()
        self.source = source
        self.calls = []
        self.last_message = None

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109 - aiogram API
        # Both answerCallbackQuery and message IO happen outside DB transactions.
        assert self.source.depth == 0
        self.calls.append(method)
        if isinstance(method, (AnswerCallbackQuery, SetMyCommands, DeleteMessage)):
            return True
        assert isinstance(
            method, (SendMessage, EditMessageText, SendPhoto, EditMessageMedia, EditMessageCaption)
        )
        photo = isinstance(method, (SendPhoto, EditMessageMedia))
        caption = (
            method.media.caption
            if isinstance(method, EditMessageMedia)
            else getattr(method, "caption", None)
        )
        self.last_message = Message(
            message_id=77,
            date=datetime.now(UTC),
            chat=Chat(id=1001, type="private"),
            from_user=User(id=bot.id, is_bot=True, first_name="Goaletto"),
            text=getattr(method, "text", None),
            caption=caption,
            photo=[
                PhotoSize(file_id="test-photo", file_unique_id="test-photo", width=256, height=256)
            ]
            if photo
            else None,
            reply_markup=method.reply_markup,
        )
        return self.last_message

    async def stream_content(self, *args, **kwargs):
        yield b""


async def test_start_claim_lineup_history_and_table_through_dispatcher(sessions):
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        await initialize_world(
            session,
            WorldOptions(
                key="dispatcher-wave",
                seed=0,
                first_kickoff=(now + timedelta(days=4)).replace(
                    hour=18, minute=0, second=0, microsecond=0
                ),
                initial_budget=50000,
            ),
            initialized_at=now,
        )
    source = Source(sessions)
    telegram = Telegram(source)
    bot = Bot("123456:FAKE_LOCAL_TEST_TOKEN", session=telegram)
    dispatcher = build_dispatcher(source, game_enabled=True)
    # A second dispatcher can be built without attaching an existing Router twice.
    build_dispatcher(source, game_enabled=True)
    user = User(id=1001, is_bot=False, first_name="Manager")
    update_id = 0

    async def command(text):
        nonlocal update_id
        update_id += 1
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=update_id,
                message=Message(
                    message_id=update_id,
                    date=now,
                    chat=Chat(id=user.id, type="private"),
                    from_user=user,
                    text=text,
                ),
            ),
        )
        assert isinstance(telegram.calls[-1], (SendMessage, SendPhoto))
        return telegram.last_message

    async def callback(data):
        nonlocal update_id
        update_id += 1
        previous = len(telegram.calls)
        await dispatcher.feed_update(
            bot,
            Update(
                update_id=update_id,
                callback_query=CallbackQuery(
                    id=f"callback-{update_id}",
                    from_user=user,
                    chat_instance="fake-chat",
                    message=telegram.last_message,
                    data=data,
                ),
            ),
        )
        assert isinstance(telegram.calls[previous], AnswerCallbackQuery)
        assert any(
            isinstance(call, (EditMessageText, EditMessageMedia, SendMessage))
            for call in telegram.calls[previous:]
        )
        return telegram.last_message

    response = await command("/start")
    assert "Выберите" in response.text
    response = await callback(response.reply_markup.inline_keyboard[0][0].callback_data)
    assert "Получить клуб" in str(response.reply_markup)
    response = await callback(response.reply_markup.inline_keyboard[0][0].callback_data)
    assert "Выберите" not in (response.text or response.caption)
    async with sessions.begin() as session:
        manager = await session.scalar(select(Manager).where(Manager.telegram_id == user.id))
        ownership = await session.scalar(
            select(OwnershipPeriod).where(OwnershipPeriod.manager_id == manager.id)
        )
        assert ownership is not None

    response = await command("/settings")
    assert "Напоминания" in response.text
    response = await callback(
        next(
            b.callback_data
            for row in response.reply_markup.inline_keyboard
            for b in row
            if b.callback_data.startswith("ntf:off:")
        )
    )
    assert "выключены" in response.text
    response = await callback(
        next(
            b.callback_data
            for row in response.reply_markup.inline_keyboard
            for b in row
            if b.callback_data.startswith("ntf:on:")
        )
    )
    assert "включены" in response.text
    async with sessions.begin() as session:
        current = await session.get(OwnershipPeriod, ownership.id)
        assert current.last_game_action_at == ownership.last_game_action_at
        assert current.expires_at == ownership.expires_at
    for cmd in ("/squad", "/history", "/table", "/club", "/lineup"):
        response = await command(cmd)
        assert "недоступ" not in (response.text or response.caption).lower()
    # Choose a captain; the weak starting coach only permits the existing normal tactic.
    panel = next(
        b.callback_data
        for row in response.reply_markup.inline_keyboard
        for b in row
        if b.text == "Капитан"
    )
    response = await callback(panel)
    response = await callback(response.reply_markup.inline_keyboard[0][0].callback_data)
    panel = next(
        b.callback_data
        for row in response.reply_markup.inline_keyboard
        for b in row
        if b.text == "Тактика"
    )
    response = await callback(panel)
    response = await callback(response.reply_markup.inline_keyboard[0][0].callback_data)
    async with sessions.begin() as session:
        rows = (await session.scalars(select(AcceptedCommand))).all()
        assert len(rows) == 1
        assert {r.telegram_update_id for r in rows} == {13}
        lineup = await session.get(ClubLineup, ownership.club_id)
        assert lineup.version == 2
    await command("/settings")
    response = await callback("lang:en")
    assert "Settings" in response.text
    for cmd in (
        "/start",
        "/help",
        "/club",
        "/squad",
        "/lineup",
        "/match",
        "/table",
        "/history",
        "/train",
        "/settings",
    ):
        response = await command(cmd)
        assert not any(
            "А" <= ch <= "я" or ch in "Ёё"
            for ch in (response.text or response.caption).replace("Язык", "")
        ), (cmd, response.text or response.caption)
    async with sessions.begin() as session:
        manager = await session.scalar(select(Manager).where(Manager.telegram_id == user.id))
        assert manager.locale == "en"
    response = await command("/timezone Belgrade")
    assert "Europe/Belgrade" in response.text and "2026" in response.text
    response = await callback("tz:Europe/Belgrade")
    assert "Europe/Belgrade" in response.text
    response = await command("/kickoff 19:15")
    assert "19:15" in response.text
    async with sessions.begin() as session:
        manager = await session.scalar(select(Manager).where(Manager.telegram_id == user.id))
        assert manager.timezone_name == "Europe/Belgrade"
        assert manager.preferred_kickoff_minute == 1155
    response = await command("/kickoff 25:00")
    assert "HH" not in response.text and "/kickoff 19:30" in response.text
    response = await command("/timezone MissingCity")
    assert "Timezone not found" in response.text
    await bot.session.close()
