"""Bounded onboarding observations; never extend ownership or infer confusion.

Failures are recorded after the game transaction rolls back. Direct response
confirmations are written only after Telegram accepts the send/edit. A missing
confirmation is unknown (including a crash after send), never a delivered screen.
"""

from datetime import datetime
from uuid import uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import (
    DIRECT_RESPONSE_EFFECT,
    JourneyEvent,
    Origin,
    Reason,
    Surface,
)
from goaletto.application.game_actions import Actor
from goaletto.infrastructure.analytics import record_event
from goaletto.infrastructure.models import JourneyEventRow, Manager, OwnershipPeriod

REJECTION_EFFECT = 31000
RESPONSE_EFFECT = DIRECT_RESPONSE_EFFECT


async def rejection(session: AsyncSession, actor: Actor, surface: Surface, reason: Reason) -> bool:
    if actor.analytics_id is None:
        return False
    await session.get(Manager, actor.manager_id, with_for_update=True)
    previous = await session.scalar(
        select(JourneyEventRow).where(
            JourneyEventRow.operation_id == actor.operation_id,
            JourneyEventRow.effect_number == REJECTION_EFFECT,
        )
    )
    if previous is not None:
        return False  # A redelivered update is the same observed failure.
    ownership = await session.scalar(
        select(OwnershipPeriod).where(
            OwnershipPeriod.manager_id == actor.manager_id,
            OwnershipPeriod.ended_at.is_(None),
            OwnershipPeriod.expires_at > actor.at,
        )
    )
    return await record_event(
        session,
        JourneyEvent(
            event_id=uuid5(actor.operation_id, "onboarding-rejection-v1"),
            name="action_rejected",
            origin=Origin.USER,
            operation_id=actor.operation_id,
            effect_number=REJECTION_EFFECT,
            surface=surface,
            target=Surface.ONBOARDING if reason == Reason.PERMISSION_DENIED else surface,
            reason=reason,
            occurred_at=actor.at,
            recorded_at=actor.at,
            analytics_manager_id=actor.analytics_id,
            ownership_id=ownership.id if ownership is not None else None,
            club_id=ownership.club_id if ownership is not None else None,
        ),
    )


async def response_accepted(
    session: AsyncSession,
    actor: Actor,
    at: datetime,
    *,
    offer: bool = False,
) -> bool:
    if actor.analytics_id is None:
        return False
    await session.get(Manager, actor.manager_id, with_for_update=True)
    previous = await session.scalar(
        select(JourneyEventRow)
        .where(
            JourneyEventRow.operation_id == actor.operation_id,
            JourneyEventRow.effect_number.in_((REJECTION_EFFECT, RESPONSE_EFFECT)),
        )
        .order_by(JourneyEventRow.effect_number.desc())
        .limit(1)
    )
    if previous is not None and previous.effect_number == RESPONSE_EFFECT:
        return False
    if not offer and previous is None:
        return False
    if offer:
        surface, target = Surface.ONBOARDING, Surface.ONBOARDING
    else:
        assert previous is not None
        surface, target = (
            Surface(previous.envelope["surface"]),
            Surface(previous.envelope["target"]),
        )
    return await record_event(
        session,
        JourneyEvent(
            event_id=uuid5(actor.operation_id, "onboarding-response-v1"),
            name="club_offer_delivered" if offer else "help_delivered",
            origin=Origin.SYSTEM,
            operation_id=actor.operation_id,
            effect_number=RESPONSE_EFFECT,
            surface=surface,
            target=target,
            occurred_at=at,
            recorded_at=at,
            analytics_manager_id=actor.analytics_id,
        ),
    )
