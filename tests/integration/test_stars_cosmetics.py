"""Real single application DB, fake Telegram payments/refunds, no real Stars charged."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerPreCheckoutQuery, RefundStarPayment, SendInvoice
from aiogram.types import (
    CallbackQuery,
    Chat,
    Message,
    PreCheckoutQuery,
    SuccessfulPayment,
    Update,
    User,
)
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from goaletto.application import stars
from goaletto.application.club_rename import action as rename_action
from goaletto.application.club_rename import screen, submit
from goaletto.application.club_view import get_owned_club
from goaletto.application.cosmetics import available_grant, ensure_vip_bonus, vip_clubs
from goaletto.application.game_actions import PermissionDenied
from goaletto.application.history import get_season_archive
from goaletto.application.notification_preferences import get_settings
from goaletto.application.outbox_delivery import Decision, check_recipient, render_text
from goaletto.bot.club_cards import club_view
from goaletto.bot.main import build_dispatcher
from goaletto.bot.result_views import table_view
from goaletto.infrastructure.models import (
    Club,
    ClubRenameReceipt,
    CosmeticGrant,
    Manager,
    OutboxMessage,
    PaySupportRequest,
    Season,
    StarOrder,
    StarPayment,
)
from goaletto.infrastructure.star_refunds import process_refund
from tests.integration.test_agent_wave import NOW, setup_world
from tests.integration.test_candidates import action
from tests.integration.test_game_dispatcher import Source, Telegram

pytestmark = pytest.mark.integration
ADMIN = 9001
VIP_PRICE = stars.PRICES["vip"]


async def rename_once(session, actor, name):
    view = await screen(session, actor)
    view = await rename_action(session, action(actor), view.ownership_id, "input", view.draft_id)
    view = await submit(session, action(actor), name)
    return await rename_action(session, action(actor), view.ownership_id, "confirm", view.draft_id)


async def pay(session, actor, product, charge=None):
    owner = (await screen(session, actor)).ownership_id
    order = await stars.create_order(session, action(actor), product, owner)
    assert await stars.precheckout(
        session,
        stars.payload(order),
        actor.telegram_id,
        order.amount,
        "XTR",
        str(uuid4()),
        actor.at,
    )
    return await stars.successful(
        session,
        value=stars.payload(order),
        payer=actor.telegram_id,
        amount=order.amount,
        currency="XTR",
        charge=charge or str(uuid4()),
        at=actor.at,
    )


async def test_invoice_validation_receipt_retry_and_immutable_terms(sessions):
    _, actor, club = await setup_world(sessions)
    async with sessions.begin() as session:
        owner = (await screen(session, actor)).ownership_id
        order = await stars.create_order(session, action(actor), "vip", owner)
        body = stars.payload(order)
        for payer, amount, currency in ((1002, 1, "XTR"), (1001, 50, "XTR"), (1001, 1, "EUR")):
            assert not await stars.precheckout(session, body, payer, amount, currency, "bad", NOW)
        assert not await vip_clubs(session, {club}, NOW)
        assert await stars.precheckout(session, body, 1001, VIP_PRICE, "XTR", "approved", NOW)
        assert await stars.precheckout(session, body, 1001, VIP_PRICE, "XTR", "approved", NOW)
        assert not await stars.precheckout(session, body, 1001, VIP_PRICE, "XTR", "second", NOW)
    with pytest.raises(RuntimeError):
        async with sessions.begin() as session:
            await stars.successful(
                session,
                value=body,
                payer=1001,
                amount=VIP_PRICE,
                currency="XTR",
                charge="retry-charge",
                at=NOW,
            )
            raise RuntimeError("rollback")
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(StarPayment)) == 0
        paid = await stars.successful(
            session,
            value=body,
            payer=1001,
            amount=VIP_PRICE,
            currency="XTR",
            charge="retry-charge",
            at=NOW,
        )
        again = await stars.successful(
            session,
            value=body,
            payer=1001,
            amount=VIP_PRICE,
            currency="XTR",
            charge="retry-charge",
            at=NOW,
        )
        assert paid.id == again.id and paid.status == "delivered"
        assert paid.vip_until == datetime(2026, 11, 12, 12, tzinfo=UTC)
        assert await vip_clubs(session, {club}, NOW) == {club}
        assert not await vip_clubs(session, {club}, paid.vip_until)
        with pytest.raises(ValueError, match="different content"):
            await stars.successful(
                session,
                value=body,
                payer=1002,
                amount=VIP_PRICE,
                currency="XTR",
                charge="retry-charge",
                at=NOW,
            )
        for sql in ("UPDATE star_payments SET amount=2", "UPDATE star_orders SET amount=2"):
            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    await session.execute(text(sql))


async def test_single_slot_purchase_race_and_offseason_only(sessions):
    result, actor, club = await setup_world(sessions)
    async with sessions.begin() as session:
        state = await screen(session, actor)
        with pytest.raises(ValueError):
            await stars.create_order(session, action(actor), "rename", state.ownership_id)
        await rename_once(session, actor, "First Albion")

    async def invoice():
        async with sessions.begin() as session:
            return (
                await stars.create_order(session, action(actor), "rename", state.ownership_id)
            ).id

    ids = await asyncio.gather(invoice(), invoice())
    assert ids[0] == ids[1]
    async with sessions.begin() as session:
        order = await session.get(StarOrder, ids[0])
        assert order.amount == 50
        assert await stars.precheckout(session, stars.payload(order), 1001, 50, "XTR", "buy", NOW)
        await stars.successful(
            session,
            value=stars.payload(order),
            payer=1001,
            amount=50,
            currency="XTR",
            charge="rename-paid",
            at=NOW,
        )
        (await session.get(Season, result.season_id)).status = "active"
    async with sessions.begin() as session:
        s = await screen(session, actor)
        assert s.credit and not s.allowed
        assert not (
            await rename_action(session, action(actor), s.ownership_id, "input", s.draft_id)
        ).allowed
        with pytest.raises(PermissionDenied):
            await submit(session, action(actor), "Midseason")
        with pytest.raises(ValueError):
            await stars.create_order(session, action(actor), "rename", s.ownership_id)
        (await session.get(Season, result.season_id)).status = "scheduled"
    async with sessions.begin() as session:
        await rename_once(session, actor, "Second Albion")
        assert await session.scalar(select(func.count()).select_from(ClubRenameReceipt)) == 2
        assert await available_grant(session, club) is None


async def test_calendar_month_vip_bonus_once_badges_and_no_gameplay_change(sessions):
    result, actor, club = await setup_world(sessions)
    # Freeze Jan31 to exercise real calendar-month end clamping; use a scheduled future season.
    at = datetime(2028, 1, 31, 15, 30, tzinfo=UTC)
    # Payment month calculation is independently exercised below with the normal active owner time.
    from goaletto.domain.ownership import calendar_months_after

    assert calendar_months_after(at, 1) == datetime(2028, 2, 29, 15, 30, tzinfo=UTC)
    async with sessions.begin() as session:
        before = (await session.get(Club, club)).budget
        paid = await pay(session, actor, "vip")
        assert (
            await session.scalar(
                select(func.count()).select_from(CosmeticGrant).where(CosmeticGrant.origin == "vip")
            )
            == 0
        )
        await rename_once(session, actor, "Initial Name")
        grant = await available_grant(session, club)
        assert grant.origin == "vip" and grant.payment_id == paid.id
        await rename_once(session, actor, "VIP Albion")
        await ensure_vip_bonus(session, club, NOW)
        assert await available_grant(session, club) is None
        assert (await session.get(Club, club)).budget == before
        card = await get_owned_club(session, action(actor))
        assert "VIP Albion 👑 VIP" in club_view(card)[0]
        state = await get_settings(session, action(actor))
        assert state.vip_until == paid.vip_until
        archive = await get_season_archive(session, action(actor), result.season_id)
        body, _ = table_view(
            archive.table,
            archive.season_id,
            archive.league_id,
            archive.tier,
            vip_clubs=archive.vip_clubs,
        )
        assert "VIP Albion 👑" in body and "👑 VIP" not in body


async def test_unfulfillable_charge_kept_for_refund_after_ownership_change(sessions):
    _, actor, club = await setup_world(sessions)
    async with sessions.begin() as session:
        owner = (await screen(session, actor)).ownership_id
        order = await stars.create_order(session, action(actor), "vip", owner)
        value = stars.payload(order)
        assert await stars.precheckout(session, value, 1001, VIP_PRICE, "XTR", "old", NOW)
        from goaletto.infrastructure.models import OwnershipPeriod

        period = await session.get(OwnershipPeriod, owner)
        period.ended_at, period.end_reason = NOW, "voluntary"
        (await session.get(Club, club)).owner_telegram_id = None
    async with sessions.begin() as session:
        paid = await stars.successful(
            session,
            value=value,
            payer=1001,
            amount=VIP_PRICE,
            currency="XTR",
            charge="late-owner",
            at=NOW,
        )
        assert paid.status == "unfulfilled" and not await vip_clubs(session, {club}, NOW)
        req = await stars.support(session, 1001, paid.id, "Ownership changed", ADMIN, NOW)
        assert req.status == "pending"
        with pytest.raises(ValueError):
            await stars.support(session, 1002, paid.id, "Other payer", ADMIN, NOW)


class RefundBot:
    def __init__(self, source, outcome="success"):
        self.source, self.outcome, self.calls = source, outcome, []

    async def refund_star_payment(self, **kw):
        assert self.source.depth == 0
        self.calls.append(kw)
        if self.outcome == "unknown":
            raise TimeoutError()
        if self.outcome == "already":
            raise TelegramBadRequest(
                method=RefundStarPayment(
                    user_id=kw["user_id"],
                    telegram_payment_charge_id=kw["telegram_payment_charge_id"],
                ),
                message="CHARGE_ALREADY_REFUNDED",
            )
        return True


@pytest.mark.parametrize("unknown", [False, True])
async def test_admin_refund_durable_unknown_retry_and_revocation(sessions, unknown):
    _, actor, club = await setup_world(sessions)
    async with sessions.begin() as session:
        await rename_once(session, actor, "Free Name")
        paid = await pay(session, actor, "vip")
        assert await available_grant(session, club)
        req = await stars.support(session, 1001, paid.id, "Please refund", ADMIN, NOW)
        assert (await stars.support(session, 1001, paid.id, "duplicate", ADMIN, NOW)).id == req.id
        with pytest.raises(ValueError):
            await stars.support_action(session, req.id, "refund", "", ADMIN, ADMIN + 1, NOW)
        await stars.support_action(session, req.id, "ask", "Why refund?", ADMIN, ADMIN, NOW)
        with pytest.raises(ValueError):
            await stars.support_answer(session, 1002, req.id, "not my request", NOW)
        await stars.support_answer(session, 1001, req.id, "Changed my mind", NOW)
        await stars.support_action(session, req.id, "refund", "", ADMIN, ADMIN, NOW)
        await stars.support_action(session, req.id, "refund", "", ADMIN, ADMIN, NOW)
    source = Source(sessions)
    bot = RefundBot(source, "unknown" if unknown else "success")
    assert await process_refund(source, bot)
    if unknown:
        async with sessions.begin() as session:
            assert (await session.get(PaySupportRequest, req.id)).status == "unknown"
            assert (await session.get(StarPayment, paid.id)).refunded_at is None
        assert not await process_refund(source, bot)  # never silently replay an unknown refund
        async with sessions.begin() as session:
            await stars.support_action(
                session, req.id, "refund", "retry after checking", ADMIN, ADMIN, NOW
            )
        bot.outcome = "already"
        assert await process_refund(source, bot)
    async with sessions.begin() as session:
        assert (await session.get(PaySupportRequest, req.id)).status == "refunded"
        assert (await session.get(StarPayment, paid.id)).refunded_at is not None
        assert not await vip_clubs(session, {club}, datetime.now(UTC))
        assert await available_grant(session, club) is None
        assert not await stars.mark_refunded(session, paid.charge_id, 1001, datetime.now(UTC))
        messages = (
            await session.scalars(
                select(OutboxMessage).where(
                    OutboxMessage.payload["kind"].astext == "payment_notice"
                )
            )
        ).all()
        for message in messages:
            assert await check_recipient(session, message, NOW) == Decision.DELIVER
            assert render_text(message.payload)
        assert {m.chat_id for m in messages} >= {paid.payer_id, ADMIN}
        (await session.get(Manager, actor.manager_id)).bot_available = False
        await session.flush()
        for message in messages:
            decision = (
                Decision.SKIP_UNAVAILABLE if message.chat_id == paid.payer_id else Decision.DELIVER
            )
            assert await check_recipient(session, message, NOW) == decision
    assert not await process_refund(source, bot)


async def test_real_dispatcher_invoice_checkout_success_support_and_admin_chat(sessions):
    from goaletto.application.match_runtime import activate_season
    from goaletto.application.world import initialize_world
    from goaletto.domain.world import WorldOptions

    now = datetime.now(UTC)
    async with sessions.begin() as session:
        world = await initialize_world(
            session,
            WorldOptions(
                key="stars-ui",
                seed=0,
                first_kickoff=(now + timedelta(days=4)).replace(
                    hour=18, minute=0, second=0, microsecond=0
                ),
            ),
            initialized_at=now,
        )
        await activate_season(session, world.season_id, now)
    source = Source(sessions)

    class PaymentsTelegram(Telegram):
        async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109 - aiogram API
            if isinstance(method, (SendInvoice, AnswerPreCheckoutQuery)):
                assert self.source.depth == 0
                self.calls.append(method)
                if isinstance(method, AnswerPreCheckoutQuery):
                    return True
                return Message(
                    message_id=80,
                    date=now,
                    chat=Chat(id=method.chat_id, type="private"),
                    text=method.title,
                )
            return await super().make_request(bot, method, timeout)

    transport = PaymentsTelegram(source)
    bot = Bot("123456:FAKE_LOCAL_TEST_TOKEN", session=transport)
    dispatcher = build_dispatcher(
        source,
        game_enabled=True,
        allowed_telegram_ids=frozenset({1001}),
        payments_admin_chat_id=ADMIN,
    )
    count = 100

    async def send(text=None, data=None, payment=None, user_id=1001, precheck=None):
        nonlocal count
        count += 1
        user = User(id=user_id, is_bot=False, first_name="Manager")
        if precheck:
            update = Update(update_id=count, pre_checkout_query=precheck)
        elif data:
            update = Update(
                update_id=count,
                callback_query=CallbackQuery(
                    id=str(count),
                    from_user=user,
                    chat_instance="test",
                    message=transport.last_message,
                    data=data,
                ),
            )
        else:
            update = Update(
                update_id=count,
                message=Message(
                    message_id=count,
                    date=now,
                    chat=Chat(id=user_id, type="private"),
                    from_user=user,
                    text=text,
                    successful_payment=payment,
                ),
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

    reply = await send("/start")
    reply = await send(data=button(reply, "en:p:"))
    await send(data=button(reply, "en:c:"))
    reply = await send("/settings")
    assert f"{VIP_PRICE} ⭐" in str(reply.reply_markup) and not any(
        (b.callback_data or "").startswith("buy:rename")
        for row in reply.reply_markup.inline_keyboard
        for b in row
    )
    await send(data=button(reply, "buy:vip:"))
    invoice = next(c for c in transport.calls if isinstance(c, SendInvoice))
    assert (
        invoice.currency == "XTR"
        and invoice.prices[0].amount == VIP_PRICE
        and invoice.provider_token == ""
    )
    async with sessions.begin() as session:
        assert await session.scalar(select(func.count()).select_from(StarPayment)) == 0
    user = User(id=1001, is_bot=False, first_name="Manager")
    await send(
        precheck=PreCheckoutQuery(
            id="wrong",
            from_user=user,
            currency="XTR",
            total_amount=50,
            invoice_payload=invoice.payload,
        )
    )
    assert not [c for c in transport.calls if isinstance(c, AnswerPreCheckoutQuery)][-1].ok
    await send(
        precheck=PreCheckoutQuery(
            id="real",
            from_user=user,
            currency="XTR",
            total_amount=VIP_PRICE,
            invoice_payload=invoice.payload,
        )
    )
    assert [c for c in transport.calls if isinstance(c, AnswerPreCheckoutQuery)][-1].ok
    paid = SuccessfulPayment(
        currency="XTR",
        total_amount=VIP_PRICE,
        invoice_payload=invoice.payload,
        telegram_payment_charge_id="ui-paid",
        provider_payment_charge_id="",
    )
    await send(payment=paid)
    await send(payment=paid)
    reply = await send("/settings")
    assert "VIP до" in reply.text
    async with sessions.begin() as session:
        payment_row = await session.scalar(select(StarPayment))
        assert await session.scalar(select(func.count()).select_from(StarPayment)) == 1
    await send(f"/paysupport {payment_row.id} return please")
    async with sessions.begin() as session:
        request = await session.scalar(select(PaySupportRequest))
        assert request.status == "pending" and request.admin_chat_id == ADMIN
    await send(f"/refund {request.id}", user_id=9002)
    async with sessions.begin() as session:
        assert (await session.get(PaySupportRequest, request.id)).status == "pending"
    await send(f"/refund {request.id}", user_id=ADMIN)
    assert await process_refund(source, RefundBot(source))
    reply = await send("/settings")
    assert "VIP до" not in reply.text
    await bot.session.close()


async def test_live_vip_badge_changes_no_match_snapshots_or_game_cash(sessions):
    from copy import deepcopy

    from goaletto.application.live_commands import load_live_screen
    from goaletto.application.match_runtime import queue_notification
    from goaletto.bot.live_views import render
    from goaletto.infrastructure.models import Fixture, Match
    from tests.integration.test_agent_wave import start_fixture

    _, actor, club = await setup_world(sessions)
    fixture_id, started = await start_fixture(sessions, club)
    current = replace(action(actor), at=started + timedelta(seconds=1))
    async with sessions.begin() as session:
        match = await session.scalar(select(Match).where(Match.fixture_id == fixture_id))
        frozen = (deepcopy(match.starting_snapshot), deepcopy(match.state))
        cash = (await session.get(Club, club)).budget
        paid = await pay(session, current, "vip")
        live = await load_live_screen(session, action(current))
        assert club in live.vip_clubs
        assert "👑 VIP" in render(live)[0]
        assert (match.starting_snapshot, match.state) == frozen
        assert (await session.get(Club, club)).budget == cash
        fixture = await session.get(Fixture, fixture_id)
        await queue_notification(
            session, fixture, "match_board", "vip-check", current.at, live.state
        )
        row = await session.scalar(
            select(OutboxMessage)
            .where(OutboxMessage.payload["kind"].astext == "match_board")
            .order_by(OutboxMessage.due_at.desc())
        )
        assert "👑 VIP" in render_text(row.payload)
        await stars.mark_refunded(session, paid.charge_id, current.telegram_id, current.at)
        assert "👑 VIP" not in render(await load_live_screen(session, action(current)))[0]
        assert (match.starting_snapshot, match.state) == frozen
