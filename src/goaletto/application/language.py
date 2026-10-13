"""Durable explicit language preference, independent of club ownership."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.game_actions import Actor, PermissionDenied, allocation_lock
from goaletto.infrastructure.models import GameActionReceipt, Manager


async def set_language(session: AsyncSession, actor: Actor, desired: str) -> str:
    if not session.in_transaction() or desired not in {"ru", "en"}:
        raise ValueError("Invalid language preference")
    await allocation_lock(session)
    manager = await session.scalar(
        select(Manager)
        .where(Manager.id == actor.manager_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if manager is None or manager.telegram_id != actor.telegram_id:
        raise PermissionDenied("Manager identity mismatch")
    effect = int(desired == "en")
    receipts = list(
        (
            await session.scalars(
                select(GameActionReceipt).where(
                    GameActionReceipt.operation_id == actor.operation_id
                )
            )
        ).all()
    )
    if receipts:
        if (
            len(receipts) != 1
            or receipts[0].manager_id != actor.manager_id
            or receipts[0].kind != "language_preferences"
            or receipts[0].effect_number != effect
        ):
            raise PermissionDenied("Language operation reused")
        return manager.locale  # An old retry must not revert a newer selection.
    session.add(
        GameActionReceipt(
            operation_id=actor.operation_id,
            effect_number=effect,
            manager_id=actor.manager_id,
            ownership_id=None,
            club_id=None,
            kind="language_preferences",
            occurred_at=actor.at,
        )
    )
    manager.locale = desired
    await session.flush()
    return desired
