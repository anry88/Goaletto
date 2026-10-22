"""Actual Dispatcher with local PostgreSQL and fake Telegram; IO outside transactions."""

from datetime import UTC, datetime, timedelta

import pytest
from aiogram import Bot
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Chat, ForceReply, Message, Update, User
from sqlalchemy import select

from goaletto.application.training import load_training
from goaletto.bot.club_cards import player_callback
from goaletto.bot.main import build_dispatcher
from goaletto.domain.world import WorldOptions
from goaletto.infrastructure.models import Club, FriendlySearch, Manager, Player, PlayerRelease
from tests.integration.test_candidates import action, funded
from tests.integration.test_game_dispatcher import Source, Telegram

pytestmark = pytest.mark.integration


async def test_search_reply_training_calendar_sale_and_release_through_real_dispatcher(sessions):
    now = datetime.now(UTC)
    _, actor, club_id = await funded(
        sessions,
        at=now,
        options=WorldOptions(
            key="management-ui",
            seed=0,
            first_kickoff=(now + timedelta(days=4)).replace(
                hour=18, minute=0, second=0, microsecond=0
            ),
            initial_budget=50000,
        ),
    )
    source = Source(sessions)

    class SearchTelegram(Telegram):
        sequence = 200

        async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109
            if isinstance(method, SendMessage) and isinstance(method.reply_markup, ForceReply):
                method = method.model_copy(update={"reply_markup": None})
            result = await super().make_request(bot, method, timeout)
            if isinstance(result, Message):
                self.sequence += 1
                self.last_message = result.model_copy(update={"message_id": self.sequence})
                return self.last_message
            return result

    transport = SearchTelegram(source)
    bot = Bot("123456:FAKE_LOCAL_TEST_TOKEN", session=transport)
    dispatcher = build_dispatcher(source, game_enabled=True)
    counter = 10000
    user = User(id=1001, is_bot=False, first_name="Manager")

    async def send(data=None, text=None, reply=None):
        nonlocal counter
        counter += 1
        if data:
            update = Update(
                update_id=counter,
                callback_query=CallbackQuery(
                    id=str(counter),
                    from_user=user,
                    chat_instance="test",
                    message=transport.last_message,
                    data=data,
                ),
            )
        else:
            update = Update(
                update_id=counter,
                message=Message(
                    message_id=counter,
                    date=now,
                    chat=Chat(id=1001, type="private"),
                    from_user=user,
                    text=text,
                    reply_to_message=reply,
                ),
            )
        await dispatcher.feed_update(bot, update)
        return transport.last_message

    def button(prefix):
        return next(
            b.callback_data
            for row in transport.last_message.reply_markup.inline_keyboard
            for b in row
            if (b.callback_data or "").startswith(prefix)
        )

    await send(text="/friendly")
    await send(data="fr:c")
    day_button = button("fr:d:")
    await send(data=day_button)
    prompt = transport.last_message
    async with sessions.begin() as s:
        context = await s.scalar(select(FriendlySearch))
        assert context.prompt_message_id == prompt.message_id
        other = await s.scalar(select(Club).where(Club.id != club_id).order_by(Club.name).limit(1))
        opponent_name = other.name
    await send(text=opponent_name, reply=prompt)
    await send(data=button("fr:o:"))
    assert "Матч назначен" in transport.last_message.text
    async with sessions.begin() as s:
        screen = await load_training(s, action(actor))
        day = next(d.day for d in screen.dates if not d.reason and d.day.date() > now.date())
        manager = await s.get(Manager, actor.manager_id)
        manager.timezone_name, manager.preferred_kickoff_minute = "Etc/GMT-3", 20 * 60
        player_id = await s.scalar(
            select(Player.person_id)
            .where(Player.club_id == club_id, Player.position != "GK")
            .limit(1)
        )
    await send(text="/train")
    await send(
        data=next(
            b.callback_data
            for row in transport.last_message.reply_markup.inline_keyboard
            for b in row
            if (b.callback_data or "").endswith(day.strftime("%Y%m%d"))
        )
    )
    await send(data=button("tr:f:7:"))
    await send(data=button("tr:s:"))
    assert "Тренировка сохранена" in transport.last_message.text
    await send(data="ca:open")
    # The selected training date may be on a later calendar page.
    if "🏋️ Тренировка" not in transport.last_message.text:
        await send(
            data=next(
                b.callback_data
                for row in transport.last_message.reply_markup.inline_keyboard
                for b in row
                if "Следующая" in b.text
            )
        )
    assert "🏋️ Тренировка" in transport.last_message.text and "20:00" in transport.last_message.text
    await send(data=player_callback(player_id, 1))
    sale = button("mk:q:")
    release = button("pr:q:")
    await send(data=sale)
    assert any(
        (b.callback_data or "").startswith("ml:")
        for row in transport.last_message.reply_markup.inline_keyboard
        for b in row
    )
    await send(data=release)
    assert (
        "Компенсация" in transport.last_message.text and "4-дневный" in transport.last_message.text
    )
    confirmation = button("pr:c:")
    await send(data=confirmation)
    assert "расторгнут" in transport.last_message.text
    await send(data=confirmation)
    async with sessions.begin() as s:
        assert (await s.get(Player, player_id)).club_id is None
        assert (
            len(
                (
                    await s.scalars(
                        select(PlayerRelease).where(PlayerRelease.confirmed_at.is_not(None))
                    )
                ).all()
            )
            == 1
        )
    await bot.session.close()
