"""Deterministic PostgreSQL race synchronization for acceptance tests.

Only lock-state proofs order concurrent transactions. Polling pauses here
avoid a busy loop; they never prove the race itself. Proof is always a real
PG lock observation (advisory try-lock or FOR UPDATE NOWAIT) or an
asyncio.Event set immediately before a blocking DB call.
"""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from goaletto.infrastructure.locks import APPEARANCE_ALLOCATION_LOCK
from goaletto.infrastructure.models import Auction, MoneyReservation, Player

_BOUND_SECONDS = 10.0
_POLL_INTERVAL_SECONDS = 0.01


async def wait_until(
    predicate: Callable[[], Awaitable[bool]],
    *,
    bound: float = _BOUND_SECONDS,
) -> None:
    """Wait until a real lock-state predicate holds; bounded, no proof by pause."""

    async def _wait() -> None:
        while not await predicate():  # noqa: ASYNC110 -- PG lock has no Event; pause avoids busy loop
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    await asyncio.wait_for(_wait(), bound)


async def allocation_held_by_other(sessions: async_sessionmaker[AsyncSession]) -> bool:
    """True when another transaction holds the shared allocation advisory lock."""

    async with sessions() as session:
        held = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": APPEARANCE_ALLOCATION_LOCK}
        )
        await session.rollback()
        return not bool(held)


async def wait_for_allocation_held(
    sessions: async_sessionmaker[AsyncSession], *, bound: float = _BOUND_SECONDS
) -> None:
    await wait_until(lambda: allocation_held_by_other(sessions), bound=bound)


def _is_lock_not_available(error: DBAPIError) -> bool:
    pgcode = getattr(error.orig, "pgcode", "")
    return pgcode == "55P03" or "could not obtain lock" in str(error).lower()


async def player_row_locked_elsewhere(
    sessions: async_sessionmaker[AsyncSession], person_id: UUID
) -> bool:
    async with sessions() as session:
        try:
            await session.scalar(
                select(Player).where(Player.person_id == person_id).with_for_update(nowait=True)
            )
        except DBAPIError as error:
            await session.rollback()
            if _is_lock_not_available(error):
                return True
            raise
        await session.rollback()
        return False


async def auction_row_locked_elsewhere(
    sessions: async_sessionmaker[AsyncSession], auction_id: UUID
) -> bool:
    async with sessions() as session:
        try:
            await session.scalar(
                select(Auction).where(Auction.id == auction_id).with_for_update(nowait=True)
            )
        except DBAPIError as error:
            await session.rollback()
            if _is_lock_not_available(error):
                return True
            raise
        await session.rollback()
        return False


async def wait_for_player_locked(
    sessions: async_sessionmaker[AsyncSession], person_id: UUID, *, bound: float = 10.0
) -> None:
    await wait_until(lambda: player_row_locked_elsewhere(sessions, person_id), bound=bound)


async def active_reservation_total(session: AsyncSession, club_id: UUID) -> int:
    return (
        await session.scalar(
            select(func.coalesce(func.sum(MoneyReservation.amount), 0)).where(
                MoneyReservation.club_id == club_id, MoneyReservation.status == "active"
            )
        )
        or 0
    )


async def gather_bounded(*coros: Awaitable[object], bound: float = 15.0) -> tuple[object, ...]:
    """Run concurrent DB workers with bounded completion; cancellations are awaited."""

    tasks = [asyncio.ensure_future(coro) for coro in coros]
    try:
        return tuple(await asyncio.wait_for(asyncio.gather(*tasks), bound))
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
