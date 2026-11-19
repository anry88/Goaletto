from datetime import UTC, datetime, timedelta

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageCaption, EditMessageText
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import func, select

from goaletto.application.match_runtime import activate_season
from goaletto.application.world import initialize_world
from goaletto.bot.main import build_dispatcher
from goaletto.domain.world import WorldOptions
from goaletto.infrastructure.models import (
    Club,
    ClubNameChoice,
    JourneyEventRow,
    OwnershipPeriod,
    Season,
)
from tests.integration.test_game_dispatcher import Source, Telegram

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("confirm", [True, False])
async def test_claim_offers_name_then_restart_confirm_or_decline_until_offseason(
    sessions, confirm, monkeypatch
):
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

    async def send(text=None, data=None, source_message=None):
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
                    message=source_message or transport.last_message,
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
    preview = button(response, "en:p:")
    offer_message = response
    preview_message = await send(data=preview)
    claim = button(preview_message, "en:c:")
    response = await send(data=claim)
    assert "Страна клуба" in (response.text or response.caption)
    assert response.text is not None
    # The same inline keyboard may already be queued in Telegram with a fresh
    # callback/update ID, even though the claim transaction has committed.
    for stale_button, stale_message in ((claim, preview_message), (preview, offer_message)):
        response = await send(data=stale_button, source_message=stale_message)
        assert "Страна клуба" in response.text
    original_request = transport.make_request

    async def old_photo_request(bot, method, **kwargs):
        if isinstance(method, EditMessageText):
            raise TelegramBadRequest(method, "Bad Request: there is no text in the message to edit")
        return await original_request(bot, method, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(transport, "make_request", old_photo_request)
        response = await send(data=claim, source_message=preview_message)
        assert isinstance(transport.calls[-1], EditMessageCaption)
        assert "Страна клуба" in response.caption
    response = await send(data="en:o:1", source_message=offer_message)
    assert "Страна клуба" in response.text
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(OwnershipPeriod)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(JourneyEventRow)
                .where(JourneyEventRow.name == "action_rejected")
            )
            == 0
        )
    country = next(
        b.callback_data
        for row in response.reply_markup.inline_keyboard
        for b in row
        if (b.callback_data or "").startswith("ct:v:") and b.callback_data.endswith(":UA")
    )
    response = await send(data=country)
    async with sessions.begin() as session:
        owner = await session.scalar(select(OwnershipPeriod))
        assert (await session.get(Club, owner.club_id)).country_code is None
    response = await send(data=button(response, "ct:c:"))
    assert "Хотите переименовать" in (response.text or response.caption)
    initial_input, initial_keep = button(response, "rn:i:"), button(response, "rn:k:")
    async with sessions.begin() as session:
        owner = await session.scalar(select(OwnershipPeriod))
        club = await session.get(Club, owner.club_id)
        original = club.name
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
        async with sessions.begin() as session:
            (await session.get(Season, result.season_id)).status = "active"
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
