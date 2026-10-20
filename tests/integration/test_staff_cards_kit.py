"""Real staff authorization and Telegram media/cache with an offline transport."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageMedia, SendMessage, SendPhoto
from aiogram.types import BufferedInputFile, CallbackQuery, Chat, Message, Update, User
from sqlalchemy import func, select

from goaletto.application.candidates import load_candidates
from goaletto.application.game_actions import PermissionDenied
from goaletto.application.onboarding import begin_game, claim_club
from goaletto.application.staff_cards import load_staff_card
from goaletto.bot.main import build_dispatcher
from goaletto.bot.staff_cards import staff_card_view
from goaletto.infrastructure.models import (
    Club,
    LedgerEntry,
    Person,
    SalaryContract,
    TelegramPortraitFile,
)
from goaletto.presentation.i18n import using_locale
from tests.integration.test_agent_wave import NOW, OPTIONS
from tests.integration.test_candidates import action, funded
from tests.integration.test_game_dispatcher import Source, Telegram
from tests.integration.test_match_careers import retire

pytestmark = pytest.mark.integration


async def test_staff_details_guard_private_offers_foreign_employee_and_original_contract(sessions):
    _, actor, club = await funded(sessions)
    async with sessions.begin() as session:
        screen = await load_candidates(session, action(actor), "staff")
        employee = screen.employee_ids[0]
        salary = await session.scalar(
            select(SalaryContract).where(
                SalaryContract.person_id == employee, SalaryContract.ended_at.is_(None)
            )
        )
        before = await session.scalar(select(func.count()).select_from(LedgerEntry))
        person, career = await retire(session, actor, club)
        own = await load_staff_card(session, action(actor), screen.ownership_id, employee)
        assert own.employed and own.terms["wage"] == salary.weekly_rate
        offer = await load_staff_card(session, action(actor), screen.ownership_id, person.id)
        assert not offer.employed and offer.terms["loyal"] == 1
        assert offer.terms["wage"] == offer.terms["normal_wage"] * 8 // 10
        assert offer.former_position and offer.former_club
        assert offer.person.appearance_identity_id == person.appearance_identity_id
        for locale, text in (("ru", "Лояльность клубу"), ("en", "Club loyalty")):
            with using_locale(locale):
                body, keyboard = staff_card_view(offer)
                assert text in body and "−20%" in body
                assert any(
                    (b.callback_data or "").startswith("cg:d:")
                    for row in keyboard.inline_keyboard
                    for b in row
                )
        assert before == await session.scalar(select(func.count()).select_from(LedgerEntry))
        with pytest.raises(PermissionDenied):
            await load_staff_card(session, action(actor), uuid4(), employee)
        entry = await begin_game(session, telegram_id=1002, update_id=99, payload=None, at=NOW)
        other = action(entry.actor)
        ownership = await claim_club(session, other, entry.offers[0].id)
        with pytest.raises(PermissionDenied):
            await load_staff_card(session, action(other), ownership.id, employee)
        with pytest.raises(ValueError, match="unavailable"):
            await load_staff_card(session, action(other), ownership.id, person.id)
        later = replace(action(actor), at=career.expires_at)
        with pytest.raises(ValueError, match="unavailable"):
            await load_staff_card(session, later, screen.ownership_id, person.id)


async def test_portrait_and_kit_reuse_file_ids_recover_invalid_cache_and_keep_single_card(sessions):
    now = datetime.now(UTC)
    _, actor, club = await funded(
        sessions,
        options=replace(
            OPTIONS, first_kickoff=(now + timedelta(days=10)).replace(second=0, microsecond=0)
        ),
        at=now,
    )
    source = Source(sessions)

    class Transport(Telegram):
        fail_media = False

        async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109 - aiogram API
            if self.fail_media and isinstance(method, (SendPhoto, EditMessageMedia)):
                raise TelegramBadRequest(method, "photo unavailable")
            if isinstance(method, EditMessageMedia) and method.media.media == "expired-file":
                assert self.source.depth == 0
                self.calls.append(method)
                raise TelegramBadRequest(method, "wrong file identifier")
            return await super().make_request(bot, method, timeout)

    transport = Transport(source)
    bot = Bot("123456:FAKE_LOCAL_TEST_TOKEN", session=transport)
    dispatcher = build_dispatcher(source, game_enabled=True)
    user = User(id=1001, is_bot=False, first_name="Manager")
    number = 100

    async def send(command=None, data=None):
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
                    text=command,
                ),
            )
            if command
            else Update(
                update_id=number,
                callback_query=CallbackQuery(
                    id=f"card-{number}",
                    from_user=user,
                    chat_instance="test",
                    message=transport.last_message,
                    data=data,
                ),
            )
        )
        await dispatcher.feed_update(bot, update)
        return transport.last_message

    def buttons(message, prefix):
        return [
            b.callback_data
            for row in message.reply_markup.inline_keyboard
            for b in row
            if (b.callback_data or "").startswith(prefix)
        ]

    club_card = await send(command="/club")
    assert club_card.photo and "#" not in club_card.caption and "(v" not in club_card.caption
    assert isinstance(transport.calls[-1], SendPhoto)
    await send(command="/club")
    assert transport.calls[-1].photo == "test-photo"
    async with sessions.begin() as session:
        c = await session.get(Club, club)
        old_primary = c.kit_primary
        c.kit_primary = "#123456" if old_primary != "#123456" else "#654321"
        c.kit_version += 1
    await send(command="/club")
    assert isinstance(transport.calls[-1].photo, BufferedInputFile)
    staff = await send(command="/staff")
    assert "Персонал клуба" in staff.text and "Подписание:" not in staff.text
    assert buttons(staff, "cg:h:") == ["cg:h:0"]
    own_cards = set(buttons(staff, "cg:v:"))
    market = await send(data="cg:h:0")
    assert "Рынок персонала" in market.text and "Кандидаты для найма" in market.text
    assert own_cards.isdisjoint(buttons(market, "cg:v:"))
    market_card = await send(data=buttons(market, "cg:v:")[0])
    assert buttons(market_card, "cg:h:") == ["cg:h:0"]
    staff = await send(data="cg:s:0")
    assert set(buttons(staff, "cg:v:")) == own_cards
    employee = buttons(staff, "cg:v:")[0]
    first = await send(data=employee)
    assert first.photo and "Зарплата:" in first.caption
    first_media = transport.calls[-1]
    assert isinstance(first_media, EditMessageMedia) and isinstance(
        first_media.media.media, BufferedInputFile
    )
    before = len(transport.calls)
    second = await send(data=employee)
    assert second.photo and transport.calls[-1].media.media == "test-photo"
    assert not any(isinstance(c, (SendMessage, SendPhoto)) for c in transport.calls[before:])
    async with sessions.begin() as session:
        person = await session.get(Person, screen_person_id(employee))
        cache = await session.scalar(
            select(TelegramPortraitFile).where(
                TelegramPortraitFile.identity_id == person.appearance_identity_id
            )
        )
        cache.file_id = "expired-file"
        identity = cache.identity_id
        assert person.appearance_identity_id == identity
    await send(data=employee)
    assert isinstance(transport.calls[-1].media.media, BufferedInputFile)
    async with sessions.begin() as session:
        assert (await session.get(TelegramPortraitFile, cache.cache_key)).file_id == "test-photo"
        assert (await session.get(Person, person.id)).appearance_identity_id == identity
    # Returning from a photo to a list keeps only one live screen and usable callbacks.
    listing = await send(data=buttons(transport.last_message, "cg:s:")[0])
    assert listing.text and not listing.photo and buttons(listing, "cg:v:")
    # A new dispatcher (process restart) still resolves the same DB-backed cache.
    dispatcher = build_dispatcher(source, game_enabled=True)
    await send(data=employee)
    assert transport.calls[-1].media.media == "test-photo"
    transport.fail_media = True
    fallback = await send(command="/club")
    assert fallback.text and not fallback.photo and "#" not in fallback.text
    fallback = await send(data=employee)
    assert fallback.text and "В штабе клуба" in fallback.text and buttons(fallback, "cg:s:")
    assert not buttons(fallback, "cg:f:")
    await bot.session.close()


def screen_person_id(data):
    from goaletto.bot.candidate_views import parse_candidate

    return parse_candidate(data)[1]
