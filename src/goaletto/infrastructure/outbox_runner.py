"""Claim/commit, fresh preflight/commit, bounded I/O, finish/commit."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.outbox_delivery import (
    LeaseLost,
    Outcome,
    TelegramTransport,
    claim_outbox,
    deliver_claimed,
    finish_delivery,
    preflight_delivery,
)
from goaletto.infrastructure.models import Manager
from goaletto.presentation.i18n import using_locale


class TransactionSource(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[AsyncSession]: ...


@dataclass(frozen=True)
class DeliveryStep:
    processed: bool
    retry_after: float | None = None


def utc_now() -> datetime:
    return datetime.now(UTC)


async def process_one_delivery(
    database: TransactionSource,
    transport: TelegramTransport,
    *,
    clock: Callable[[], datetime] = utc_now,
) -> DeliveryStep:
    token = str(uuid4())  # New token for every claim, including after restart.
    async with database.transaction() as session:
        claimed = await claim_outbox(session, clock(), lease_owner=token)
    if not claimed:
        return DeliveryStep(False)
    row = claimed[0]
    message_id, attempt = row.id, row.attempts
    try:
        async with database.transaction() as session:
            ready = await preflight_delivery(
                session, message_id, clock(), lease_owner=token, attempt=attempt
            )
            chosen = await session.scalar(
                select(Manager.locale).where(Manager.telegram_id == row.chat_id)
            )
        if ready is None:
            return DeliveryStep(True)
        row, board_id = ready
        try:
            with using_locale(chosen if chosen in {"ru", "en"} else "ru"):
                async with asyncio.timeout(30):
                    outcome, telegram_id, retry_after = await deliver_claimed(
                        row, transport, clock(), existing_board_message_id=board_id
                    )
        except TimeoutError:
            outcome, telegram_id, retry_after = Outcome.UNKNOWN, None, None
        async with database.transaction() as session:
            await finish_delivery(
                session,
                message_id,
                clock(),
                outcome,
                lease_owner=token,
                attempt=attempt,
                telegram_message_id=telegram_id,
                retry_after=retry_after,
            )
        # Pause this sender on Telegram 429; the game worker continues independently.
        return DeliveryStep(True, retry_after if outcome == Outcome.RATE_LIMITED else None)
    except LeaseLost:
        # Another claim now owns the receipt. Never overwrite its completion.
        return DeliveryStep(True)
