"""One approved refund at a time, outside transactions; unknown calls require admin retry."""

from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select

from goaletto.application.game_actions import allocation_lock
from goaletto.application.stars import mark_refunded, notice
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.models import PaySupportRequest, StarPayment


async def process_refund(database: Database, bot: Bot) -> bool:
    at = datetime.now(UTC)
    async with database.transaction() as session:
        await allocation_lock(session)
        stale = (
            await session.scalars(
                select(PaySupportRequest)
                .where(
                    PaySupportRequest.status == "processing", PaySupportRequest.lease_until <= at
                )
                .limit(10)
                .with_for_update()
            )
        ).all()
        for request in stale:
            request.status = "unknown"
            paid = await session.get(StarPayment, request.payment_id)
            assert paid is not None
            notice(
                session,
                "refund-unknown:" + str(request.attempts),
                request.admin_chat_id,
                paid,
                f"Goaletto: исход возврата {request.id} неизвестен после сбоя. "
                f"Проверить и повторить /refund {request.id}.",
                at,
                request,
            )
        row = await session.scalar(
            select(PaySupportRequest)
            .where(PaySupportRequest.status == "approved")
            .order_by(PaySupportRequest.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return False
        paid = await session.get(StarPayment, row.payment_id)
        assert paid is not None
        if paid.refunded_at:
            row.status = "refunded"
            return True
        row.status = "processing"
        row.lease_until = at + timedelta(seconds=60)
        row.attempts += 1
        request_id, attempt, payer, charge = row.id, row.attempts, paid.payer_id, paid.charge_id
    outcome = "unknown"
    try:
        if await bot.refund_star_payment(
            user_id=payer, telegram_payment_charge_id=charge, request_timeout=20
        ):
            outcome = "refunded"
    except TelegramBadRequest as error:
        outcome = "refunded" if "CHARGE_ALREADY_REFUNDED" in error.message.upper() else "failed"
    except Exception:
        # Transport/timeout/termination after send cannot prove failure. No automatic second call.
        outcome = "unknown"
    async with database.transaction() as session:
        await allocation_lock(session)
        row = await session.get(PaySupportRequest, request_id, with_for_update=True)
        assert row is not None
        if row.attempts != attempt or row.status == "refunded":
            return True
        if outcome == "refunded":
            await mark_refunded(session, charge, payer, datetime.now(UTC))
        else:
            row.status = outcome
            paid = await session.get(StarPayment, row.payment_id)
            assert paid is not None
            notice(
                session,
                "refund-outcome:" + str(attempt),
                row.admin_chat_id,
                paid,
                f"Goaletto: возврат {request_id}: {outcome}. После проверки можно "
                f"повторить /refund {request_id}.",
                datetime.now(UTC),
                row,
            )
        row.audit = [
            *row.audit,
            {
                "action": "refund_result",
                "attempt": attempt,
                "outcome": outcome,
                "at": datetime.now(UTC).isoformat(),
            },
        ]
    return True
