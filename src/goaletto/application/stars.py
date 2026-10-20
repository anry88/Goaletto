"""Stars orders and receipts. Only successful_payment grants services; no network IO."""

from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.cosmetics import (
    available_grant,
    ensure_initial,
    ensure_vip_bonus,
    vip_payment,
)
from goaletto.application.game_actions import Actor, allocation_lock, owned_club
from goaletto.domain.ownership import calendar_months_after
from goaletto.infrastructure.models import (
    CosmeticGrant,
    OutboxMessage,
    OwnershipPeriod,
    PaySupportRequest,
    StarOrder,
    StarPayment,
)

PRICES = {"rename": 50, "vip": 100}


def payload(order: StarOrder) -> str:
    return "glt:" + str(order.id)


def order_id(value: str) -> UUID | None:
    try:
        return UUID(value[4:]) if value.startswith("glt:") else None
    except ValueError:
        return None


async def eligible(session: AsyncSession, order: StarOrder, at: datetime) -> bool:
    owner = await session.get(OwnershipPeriod, order.ownership_id)
    if owner is None or owner.ended_at is not None or owner.expires_at <= at:
        return False
    if order.product == "rename":
        return await available_grant(session, order.club_id) is None
    return await vip_payment(session, order.club_id, at) is None


async def create_order(
    session: AsyncSession, actor: Actor, product: str, owner_id: UUID
) -> StarOrder:
    if product not in PRICES:
        raise ValueError("Unknown Stars product")
    owner, club = await owned_club(session, actor)
    if owner.id != owner_id:
        raise ValueError("Stale ownership")
    await ensure_initial(session, owner, actor.at)
    await ensure_vip_bonus(session, club.id, actor.at)
    pending = await session.scalar(
        select(StarOrder)
        .where(
            StarOrder.club_id == club.id,
            StarOrder.product == product,
            StarOrder.status.in_(("created", "approved")),
            StarOrder.expires_at > actor.at,
        )
        .order_by(StarOrder.created_at)
        .limit(1)
    )
    if pending and pending.amount != PRICES[product]:
        if pending.status == "approved":
            raise ValueError("An earlier approved payment is still pending")
        pending.status = "cancelled"
        await session.flush()
        pending = None
    if pending:
        if not await eligible(session, pending, actor.at):
            raise ValueError("Product is already available")
        if pending.ownership_id != owner.id:
            raise ValueError("Another payment is pending")
        return pending
    row = StarOrder(
        id=uuid5(actor.operation_id, "stars:" + product),
        manager_id=actor.manager_id,
        payer_id=actor.telegram_id,
        club_id=club.id,
        ownership_id=owner.id,
        product=product,
        amount=PRICES[product],
        currency="XTR",
        status="created",
        created_at=actor.at,
        expires_at=actor.at + timedelta(minutes=30),
    )
    existing = await session.get(StarOrder, row.id)
    if existing:
        if existing.amount != PRICES[product] or existing.status not in {"created", "approved"}:
            raise ValueError("Invoice price has changed")
        return existing
    if not await eligible(session, row, actor.at):
        raise ValueError("Product is already available")
    session.add(row)
    await session.flush()
    return row


async def precheckout(
    session: AsyncSession,
    value: str,
    payer: int,
    amount: int,
    currency: str,
    query_id: str,
    at: datetime,
) -> bool:
    await allocation_lock(session)
    key = order_id(value)
    row = await session.get(StarOrder, key, with_for_update=True) if key else None
    if row is None or (row.payer_id, row.amount, row.currency) != (payer, amount, currency):
        return False
    if row.amount != PRICES.get(row.product):
        return False
    if row.expires_at <= at or row.status not in {"created", "approved"}:
        return False
    same_query = await session.scalar(select(StarOrder.id).where(StarOrder.precheck_id == query_id))
    if same_query is not None and same_query != row.id:
        return False
    if row.precheck_id is not None and row.precheck_id != query_id:
        return False
    if not await eligible(session, row, at):
        return False
    row.status, row.precheck_id = "approved", query_id
    return True


def notice(
    session: AsyncSession,
    key: str,
    chat: int,
    payment: StarPayment,
    body: str,
    at: datetime,
    request: PaySupportRequest | None = None,
) -> None:
    session.add(
        OutboxMessage(
            id=uuid5(payment.id, key),
            dedupe_key="stars:" + str(payment.id) + ":" + key,
            chat_id=chat,
            due_at=at,
            priority=90,
            payload={
                "version": 1,
                "kind": "payment_notice",
                "payment_id": str(payment.id),
                "request_id": str(request.id) if request else None,
                "body": body,
            },
        )
    )


async def successful(
    session: AsyncSession,
    *,
    value: str,
    payer: int,
    amount: int,
    currency: str,
    charge: str,
    at: datetime,
) -> StarPayment:
    await allocation_lock(session)
    if not charge or len(charge) > 256 or len(value) > 128 or amount <= 0 or payer <= 0:
        raise ValueError("Invalid payment receipt")
    old = await session.scalar(select(StarPayment).where(StarPayment.charge_id == charge))
    if old:
        if (old.payer_id, old.amount, old.currency, old.invoice_payload) != (
            payer,
            amount,
            currency,
            value,
        ):
            raise ValueError("Payment charge reused with different content")
        return old
    key = order_id(value)
    order = await session.get(StarOrder, key, with_for_update=True) if key else None
    valid = (
        order is not None
        and order.payer_id == payer
        and order.amount == amount
        and order.currency == currency == "XTR"
        and order.status == "approved"
        and await eligible(session, order, at)
    )
    paid = StarPayment(
        id=uuid5(NAMESPACE_URL, "goaletto:stars:" + charge),
        order_id=order.id if order else None,
        charge_id=charge,
        payer_id=payer,
        club_id=order.club_id if order else None,
        product=order.product if order else "unknown",
        amount=amount,
        currency=currency,
        invoice_payload=value,
        status="delivered" if valid else "unfulfilled",
        received_at=at,
        vip_until=calendar_months_after(at, 1)
        if valid and order is not None and order.product == "vip"
        else None,
    )
    if session.info.get("production"):
        from goaletto.application.data_protection import protect

        await protect(session, at)
    session.add(paid)
    await session.flush()
    if valid and order is not None:
        order.status = "paid"
        if order.product == "vip":
            await session.flush()
            await ensure_vip_bonus(session, order.club_id, at)
        else:
            session.add(
                CosmeticGrant(
                    id=uuid5(paid.id, "rename"),
                    club_id=order.club_id,
                    ownership_id=order.ownership_id,
                    payment_id=paid.id,
                    origin="stars",
                    status="available",
                    created_at=at,
                )
            )
        body = (
            "Оплата принята. VIP клуба активен на календарный месяц."
            if order.product == "vip"
            else "Оплата принята. Одна попытка переименования доступна в межсезонье."
        )
    else:
        body = (
            "Оплата получена, но услуга не выдана: условия покупки "
            "изменились. Для возврата откройте /paysupport."
        )
    notice(
        session,
        "received",
        payer,
        paid,
        body + "\nID: " + str(paid.id) + "\n/settings · /paysupport",
        at,
    )
    return paid


async def support(
    session: AsyncSession, payer: int, payment_id: UUID, reason: str, admin_chat: int, at: datetime
) -> PaySupportRequest:
    await allocation_lock(session)
    if not admin_chat or not reason.strip() or len(reason) > 1000:
        raise ValueError("Payment support needs a reason and a configured administrator")
    paid = await session.get(StarPayment, payment_id, with_for_update=True)
    if paid is None or paid.payer_id != payer or paid.refunded_at is not None:
        raise ValueError("Payment not found")
    old = await session.scalar(
        select(PaySupportRequest).where(PaySupportRequest.payment_id == payment_id)
    )
    if old:
        return old
    row = PaySupportRequest(
        id=uuid4(),
        payment_id=paid.id,
        payer_id=payer,
        admin_chat_id=admin_chat,
        reason=reason.strip(),
        status="pending",
        created_at=at,
        audit=[],
    )
    session.add(row)
    await session.flush()

    text = (
        f"Goaletto: запрос возврата {row.id}\nПокупка: {paid.product}, "
        f"{paid.amount} {paid.currency}. "
        f"Пользователь: {payer}.\n{row.reason}\n"
        f"/refund {row.id} — одобрить\n/reject {row.id} причина — отклонить\n/ask {row.id} вопрос"
    )
    notice(session, "support:" + str(row.id), admin_chat, paid, text, at, row)
    return row


async def support_action(
    session: AsyncSession,
    request_id: UUID,
    action: str,
    text: str,
    actor_id: int,
    admin_chat: int,
    at: datetime,
) -> PaySupportRequest:
    await allocation_lock(session)
    row = await session.get(PaySupportRequest, request_id, with_for_update=True)
    if (
        row is None
        or row.admin_chat_id != admin_chat
        or not admin_chat
        or (admin_chat > 0 and actor_id != admin_chat)
    ):
        raise ValueError("Support request not found")
    paid = await session.get(StarPayment, row.payment_id, with_for_update=True)
    assert paid is not None
    if row.status == "refunded" or paid.refunded_at is not None:
        return row
    if action in {"reject", "ask"} and not text.strip():
        raise ValueError("Administrator comment is required")
    if action == "refund":
        if row.status in {"approved", "processing"}:
            return row
        row.status = "approved"
        body = "Возврат подтверждён администратором и поставлен в очередь."
    elif action == "reject":
        if row.status in {"approved", "processing"}:
            raise ValueError("Refund already approved")
        row.status = "rejected"
        body = "Запрос возврата отклонён: " + text
    elif action == "ask":
        if row.status in {"approved", "processing"}:
            raise ValueError("Refund already approved")
        row.status = "info"
        body = f"Вопрос по возврату {row.id}: {text}\n/answer {row.id} ответ"
    else:
        raise ValueError("Unknown support action")
    row.decided_by = actor_id
    row.audit = [
        *row.audit,
        {"action": action, "by": actor_id, "at": at.isoformat(), "text": text[:1000]},
    ]
    notice(
        session,
        "decision:" + str(row.id) + ":" + str(len(row.audit)),
        paid.payer_id,
        paid,
        body,
        at,
        row,
    )
    return row


async def support_answer(
    session: AsyncSession, payer: int, request_id: UUID, text: str, at: datetime
) -> None:
    await allocation_lock(session)
    row = await session.get(PaySupportRequest, request_id, with_for_update=True)
    if row is None or row.payer_id != payer or row.status != "info" or not 1 <= len(text) <= 1000:
        raise ValueError("No requested payment information")
    row.status = "pending"
    row.audit = [*row.audit, {"action": "answer", "by": payer, "at": at.isoformat(), "text": text}]
    paid = await session.get(StarPayment, row.payment_id)
    assert paid is not None

    notice(
        session,
        "answer:" + str(row.id) + ":" + str(len(row.audit)),
        row.admin_chat_id,
        paid,
        f"Ответ по Goaletto {row.id}: {text}\n/refund {row.id}\n/reject {row.id} причина",
        at,
        row,
    )


async def mark_refunded(session: AsyncSession, charge: str, payer: int, at: datetime) -> bool:
    await allocation_lock(session)
    paid = await session.scalar(
        select(StarPayment).where(StarPayment.charge_id == charge).with_for_update()
    )
    if paid is None or paid.payer_id != payer:
        raise ValueError("Refund does not match a stored payment")
    if paid.refunded_at is not None:
        return False
    paid.refunded_at = at
    grants = (
        await session.scalars(
            select(CosmeticGrant)
            .where(CosmeticGrant.payment_id == paid.id, CosmeticGrant.status == "available")
            .with_for_update()
        )
    ).all()
    for grant in grants:
        grant.status = "revoked"
    req = await session.scalar(
        select(PaySupportRequest).where(PaySupportRequest.payment_id == paid.id).with_for_update()
    )
    if req:
        req.status = "refunded"
    notice(
        session,
        "refunded",
        payer,
        paid,
        "Возврат Stars выполнен. Неиспользованные права этой покупки отключены.",
        at,
        req,
    )
    return True
