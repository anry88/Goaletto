"""Operational request observations; never gameplay state or user analytics."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar
from uuid import UUID, uuid4

from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.infrastructure.models import RuntimeRequest

logger = logging.getLogger(__name__)
T = TypeVar("T")


class Transactions(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[AsyncSession]: ...


def now() -> datetime:
    return datetime.now(UTC)


class RequestTiming:
    def __init__(self, database: Transactions, operation_id: UUID, route: str) -> None:
        self.database = database
        self.id = uuid4()  # A transport retry is another attempt of the same operation.
        self.values: dict[str, Any] = {
            "id": self.id,
            "operation_id": operation_id,
            "route": route,
            "received_at": now(),
            "business_outcome": "unknown",
            "delivery_outcome": "not_started",
        }
        self.persisted = False
        self.disabled = False

    async def save(self) -> None:
        if self.disabled:
            return
        try:
            async with asyncio.timeout(1), self.database.transaction() as session:
                if not self.persisted:
                    session.add(RuntimeRequest(**self.values))
                else:
                    await session.execute(
                        update(RuntimeRequest)
                        .where(RuntimeRequest.id == self.id)
                        .values(**self.values)
                    )
            self.persisted = True
        except Exception as error:
            self.disabled = True
            # Observability loss must not roll back a committed game command or
            # prevent delivery. No exception text/IDs/Telegram payload in logs.
            logger.warning("Request observation unavailable (%s)", type(error).__name__)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        try:
            async with self.database.transaction() as session:
                yield session
        except BaseException as error:
            self.values.pop("command_id", None)  # A rolled-back insert has no FK target.
            # Connection loss during COMMIT can have an unknown outcome.
            self.values["business_outcome"] = (
                "rejected" if isinstance(error, ValueError) else "unknown"
            )
            await self.save()
            raise
        else:
            # This is observed AFTER the actual commit returns, not accepted_at
            # or a timestamp written before a transaction commits.
            self.values.update(committed_at=now(), business_outcome="committed")
            await self.save()

    def bind(self, fixture_id: UUID, command_id: UUID | None = None) -> None:
        self.values["fixture_id"] = fixture_id
        if command_id is not None:
            self.values["command_id"] = command_id

    def render(self, function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        self.values["render_started_at"] = now()
        try:
            return function(*args, **kwargs)
        finally:
            self.values["rendered_at"] = now()

    async def send(self, function: Callable[[], Awaitable[T]]) -> T:
        self.values.update(network_started_at=now(), delivery_outcome="sending")
        await self.save()  # A process dying after this point has an unknown send.
        try:
            result = await function()
        except (TelegramNetworkError, TimeoutError):
            self.values["delivery_outcome"] = "unknown"
            raise
        except TelegramAPIError:
            self.values["delivery_outcome"] = "rejected"
            raise
        except BaseException:
            self.values["delivery_outcome"] = "unknown"
            raise
        else:
            self.values["delivery_outcome"] = "accepted"
            return result
        finally:
            self.values["finished_at"] = now()
            await self.save()


@asynccontextmanager
async def observe_request(
    database: Transactions, operation_id: UUID, route: str
) -> AsyncIterator[RequestTiming]:
    timing = RequestTiming(database, operation_id, route)
    await timing.save()
    try:
        yield timing
    finally:
        if "finished_at" not in timing.values:
            timing.values["finished_at"] = now()
            await timing.save()
