"""Private Stars invoices and payer support; admin commands work only in configured chat."""

from datetime import UTC, datetime
from uuid import UUID

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    Update,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application import stars
from goaletto.application.game_actions import actor_for
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.models import StarPayment
from goaletto.presentation.i18n import tr

router = Router(name="stars-payments")


@router.callback_query(F.data.startswith("buy:"))
async def buy(
    query: CallbackQuery,
    database: Database,
    event_update: Update,
    payments_admin_chat_id: int = 0,
    payments_enabled: bool = True,
) -> None:
    await query.answer()
    if not isinstance(query.message, Message) or query.message.chat.type != "private":
        return
    if not payments_enabled or not payments_admin_chat_id:
        await query.message.answer(tr("Покупки временно недоступны."))
        return
    from goaletto.bot.result_views import expand_id

    try:
        _, product, token = (query.data or "").split(":")
        async with database.transaction() as session:
            actor = await actor_for(
                session, query.from_user.id, event_update.update_id, datetime.now(UTC)
            )
            order = await stars.create_order(session, actor, product, expand_id(token))
            value, amount = stars.payload(order), order.amount
    except (ValueError, PermissionError):
        await query.message.answer(
            tr("Покупка уже доступна или кнопка устарела. Откройте /settings.")
        )
        return
    title = tr("Переименование клуба") if product == "rename" else tr("👑 VIP статус")
    description = (
        tr("Одно переименование клуба в межсезонье. В запасе может быть только одно.")
        if product == "rename"
        else tr(
            "Видимый другим статус клуба и способ поддержать проект. "
            "В межсезонье позволяет переименовать клуб. Срок — один календарный месяц."
        )
    )
    assert query.bot is not None
    await query.bot.send_invoice(
        chat_id=query.from_user.id,
        title=title,
        description=description,
        payload=value,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=title, amount=amount)],
        start_parameter="goaletto-stars",
    )


@router.pre_checkout_query()
async def checkout(
    query: PreCheckoutQuery,
    database: Database,
    payments_enabled: bool = True,
    payments_admin_chat_id: int = 0,
    payment_allowed_ids: frozenset[int] = frozenset(),
) -> None:
    ok = False
    if (
        payments_enabled
        and payments_admin_chat_id
        and (not payment_allowed_ids or query.from_user.id in payment_allowed_ids)
    ):
        async with database.transaction() as session:
            ok = await stars.precheckout(
                session,
                query.invoice_payload,
                query.from_user.id,
                query.total_amount,
                query.currency,
                query.id,
                datetime.now(UTC),
            )
    await query.answer(
        ok=ok,
        error_message=None
        if ok
        else tr("Счёт устарел или покупка больше не нужна. Откройте /settings."),
    )


@router.message(F.successful_payment)
async def payment(message: Message, database: Database) -> None:
    if message.from_user is None or message.successful_payment is None:
        return
    p = message.successful_payment
    async with database.transaction() as session:
        await stars.successful(
            session,
            value=p.invoice_payload,
            payer=message.from_user.id,
            amount=p.total_amount,
            currency=p.currency,
            charge=p.telegram_payment_charge_id,
            at=datetime.now(UTC),
        )


@router.message(F.refunded_payment)
async def refunded(message: Message, database: Database) -> None:
    if message.refunded_payment is None:
        return
    p = message.refunded_payment
    # Refund service messages may be sent by the bot itself; the private recipient is the payer.
    async with database.transaction() as session:
        await stars.mark_refunded(
            session, p.telegram_payment_charge_id, message.chat.id, datetime.now(UTC)
        )


async def purchase_list(
    session: AsyncSession, payer: int, page: int = 0
) -> tuple[str, InlineKeyboardMarkup | None]:
    from sqlalchemy import func

    count = (
        await session.scalar(
            select(func.count())
            .select_from(StarPayment)
            .where(StarPayment.payer_id == payer, StarPayment.refunded_at.is_(None))
        )
        or 0
    )
    pages = max(1, (count + 9) // 10)
    page = max(0, min(page, pages - 1))
    rows = (
        await session.scalars(
            select(StarPayment)
            .where(StarPayment.payer_id == payer, StarPayment.refunded_at.is_(None))
            .order_by(StarPayment.received_at.desc(), StarPayment.id)
            .offset(page * 10)
            .limit(10)
        )
    ).all()
    body = (
        tr("Покупок не найдено.")
        if not rows
        else tr(
            "Нажмите кнопку покупки, вставьте скопированную команду и допишите причину возврата."
        )
        + "\n"
        + "\n".join(
            f"{r.received_at:%d.%m.%Y}: "
            f"{tr('👑 VIP статус') if r.product == 'vip' else tr('Переименование клуба')} "
            f"— {r.amount} ⭐"
            for r in rows
        )
    )
    buttons = [
        InlineKeyboardButton(text=label, callback_data=f"pay:list:{p}")
        for label, p in (("◀️", page - 1), ("▶️", page + 1))
        if 0 <= p < pages
    ]
    purchases = [
        [
            InlineKeyboardButton(
                text=f"📋 {r.received_at:%d.%m} · "
                f"{tr('👑 VIP статус') if r.product == 'vip' else tr('Переименование клуба')} "
                f"· {r.amount} ⭐",
                copy_text=CopyTextButton(text=f"/paysupport {r.id} "),
            )
        ]
        for r in rows
    ]
    if buttons:
        purchases.append(buttons)
    return body, InlineKeyboardMarkup(inline_keyboard=purchases) if purchases else None


@router.message(Command("paysupport"), F.chat.type == "private")
async def paysupport(message: Message, database: Database, payments_admin_chat_id: int = 0) -> None:
    if message.from_user is None:
        return
    args = (message.text or "").partition(" ")[2].strip()
    keyboard = None
    try:
        async with database.transaction() as session:
            if args:
                key, _, reason = args.partition(" ")
                request = await stars.support(
                    session,
                    message.from_user.id,
                    UUID(key),
                    reason,
                    payments_admin_chat_id,
                    datetime.now(UTC),
                )
                body = tr(
                    "Запрос возврата {id}: {status}.", id=str(request.id), status=request.status
                )
            else:
                body, keyboard = await purchase_list(session, message.from_user.id)
    except ValueError:
        body = tr("Покупка не найдена или запрос некорректен. Используйте /paysupport ID причина.")
    await message.answer(body, reply_markup=keyboard)


@router.message(Command("answer"), F.chat.type == "private")
async def answer(message: Message, database: Database) -> None:
    if message.from_user is None:
        return
    try:
        key, _, body = (message.text or "").partition(" ")[2].strip().partition(" ")
        async with database.transaction() as session:
            await stars.support_answer(
                session, message.from_user.id, UUID(key), body, datetime.now(UTC)
            )
        text = tr("Ответ по возврату передан администратору.")
    except ValueError:
        text = tr("Для ответа используйте /answer ID текст после вопроса администратора.")
    await message.answer(text)


@router.message(Command("refund", "reject", "ask"))
async def admin(message: Message, database: Database, payments_admin_chat_id: int = 0) -> None:
    if (
        not payments_admin_chat_id
        or message.chat.id != payments_admin_chat_id
        or message.from_user is None
    ):
        return
    if payments_admin_chat_id > 0:
        authorized = message.from_user.id == payments_admin_chat_id
    else:
        assert message.bot is not None
        member = await message.bot.get_chat_member(message.chat.id, message.from_user.id)
        authorized = member.status in {"administrator", "creator"}
    if not authorized:
        return
    try:
        action, _, tail = (message.text or "").partition(" ")
        key, _, body = tail.strip().partition(" ")
        if len(body) > 1000:
            raise ValueError("Comment too long")
        async with database.transaction() as session:
            row = await stars.support_action(
                session,
                UUID(key),
                action[1:].split("@")[0],
                body,
                message.from_user.id,
                payments_admin_chat_id,
                datetime.now(UTC),
            )
            status = row.status
        text = f"Goaletto: {key} — {status}"
    except ValueError:
        text = "Goaletto: request/action invalid. /refund ID | /reject ID reason | /ask ID question"
    await message.answer(text)


@router.callback_query((F.data == "pay:support") | F.data.startswith("pay:list:"))
async def support_button(query: CallbackQuery, database: Database) -> None:
    await query.answer()
    if not isinstance(query.message, Message) or query.message.chat.type != "private":
        return
    page = 0
    if query.data != "pay:support":
        token = (query.data or "").removeprefix("pay:list:")
        if not token.isascii() or not token.isdigit() or len(token) > 6:
            return
        page = int(token)
    async with database.transaction() as session:
        body, keyboard = await purchase_list(session, query.from_user.id, page)
    await query.message.answer(body, reply_markup=keyboard)
