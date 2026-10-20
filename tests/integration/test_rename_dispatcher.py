from datetime import UTC, datetime, timedelta

import pytest
from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import select

from goaletto.application.match_runtime import activate_season
from goaletto.application.world import initialize_world
from goaletto.bot.main import build_dispatcher
from goaletto.domain.world import WorldOptions
from goaletto.infrastructure.models import Club, ClubNameChoice, OwnershipPeriod, Season
from tests.integration.test_game_dispatcher import Source, Telegram

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("confirm", [True, False])
async def test_claim_offers_name_then_restart_confirm_or_decline_until_offseason(sessions, confirm):
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        result = await initialize_world(
            session,
            WorldOptions(
                key="rename-ui",
                seed=0,
                first_kickoff=(now + timedelta(days=4)).replace(
                    hour=18, minute=0, second=0, microsecond=0
                ),
            ),
            initialized_at=now,
        )
        await activate_season(session, result.season_id, now)
    source = Source(sessions)
    transport = Telegram(source)
    bot = Bot("123456:FAKE_LOCAL_TEST_TOKEN", session=transport)
    dispatcher = build_dispatcher(source, game_enabled=True)
    user = User(id=1001, is_bot=False, first_name="Manager")
    number = 100

    async def send(text=None, data=None):
        nonlocal number
        number += 1
        update = (
            Update(
                update_id=number,
                message=Message(
                    message_id=number,
                    date=now,
                    chat=Chat(id=1001, type="private"),
                    from_user=user,
                    text=text,
                ),
            )
            if text is not None
            else Update(
                update_id=number,
                callback_query=CallbackQuery(
                    id=f"rename-{number}",
                    from_user=user,
                    chat_instance="test",
                    message=transport.last_message,
                    data=data,
                ),
            )
        )
        await dispatcher.feed_update(bot, update)
        return transport.last_message

    def button(message, prefix):
        return next(
            b.callback_data
            for row in message.reply_markup.inline_keyboard
            for b in row
            if (b.callback_data or "").startswith(prefix)
        )

    response = await send("/start")
    response = await send(data=button(response, "en:p:"))
    response = await send(data=button(response, "en:c:"))
    assert "Хотите переименовать" in (response.text or response.caption)
    initial_input, initial_keep = button(response, "rn:i:"), button(response, "rn:k:")
    async with sessions.begin() as session:
        owner = await session.scalar(select(OwnershipPeriod))
        club = await session.get(Club, owner.club_id)
        original = club.name
        # Midseason refusal: reopening any path must wait for a real offseason.
        if not confirm:
            (await session.get(Season, result.season_id)).status = "active"
    if confirm:
        await send(data=initial_input)
        response = await send("<b>Bad name</b>")
        assert "недопустимые" in response.text
        response = await send("  New   Albion FC  ")
        assert "New Albion FC" in response.text
        async with sessions.begin() as session:
            assert (await session.get(Club, club.id)).name == original
        confirmation = button(response, "rn:c:")
        dispatcher = build_dispatcher(source, game_enabled=True)
        response = await send(data=confirmation)
        assert "сохранено" in response.text
        await send(data=confirmation)
        await send(data=initial_input)
        async with sessions.begin() as session:
            assert (await session.get(Club, club.id)).name == "New Albion FC"
            assert (await session.get(ClubNameChoice, owner.id)).state == "done"
    else:
        response = await send(data=initial_keep)
        assert "межсезонье" in (response.text or response.caption)
        response = await send("/rename")
        assert not any(
            (b.callback_data or "").startswith("rn:i:")
            for row in response.reply_markup.inline_keyboard
            for b in row
        )
        await send(data=initial_input)
        async with sessions.begin() as session:
            assert (await session.get(Club, club.id)).name == original
            assert (await session.get(ClubNameChoice, owner.id)).state == "declined"
            (await session.get(Season, result.season_id)).status = "scheduled"
        response = await send("/rename")
        assert button(response, "rn:i:")
    await bot.session.close()
