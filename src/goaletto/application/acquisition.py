"""Automatic deep-link sources and transactional start attribution before club claim."""

import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import JourneyEvent, Origin, Surface, require_utc
from goaletto.infrastructure.analytics import pseudonym_for, record_event
from goaletto.infrastructure.models import (
    AcquisitionCampaign,
    AcquisitionFirstTouch,
    AcquisitionTouch,
    Manager,
)

SOURCES = frozenset({"telegram_channel", "telegram_ads", "community", "referral", "other"})
CODE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
PLACEMENT = re.compile(r"[a-z0-9_-]{1,80}\Z")


class TouchKind(StrEnum):
    DIRECT = "direct"
    UNKNOWN = "unknown"
    KNOWN = "known"


def telegram_operation(update_id: int) -> UUID:
    if type(update_id) is not int or not 0 <= update_id < 2**63:
        raise ValueError("Invalid Telegram update identity")
    return uuid5(NAMESPACE_URL, f"goaletto:telegram-update:v1:{update_id}")


def campaign_link(code: str, bot_username: str = "GoalettoBot") -> str:
    if CODE.fullmatch(code) is None or re.fullmatch(r"[A-Za-z0-9_]{5,32}", bot_username) is None:
        raise ValueError("Invalid deep link")
    return f"https://t.me/{bot_username}?start={code}"


async def create_campaign(
    session: AsyncSession,
    source: str,
    placement: str,
    published_on: date,
    at: datetime,
) -> AcquisitionCampaign:
    require_utc(at)
    if (
        not session.in_transaction()
        or source not in SOURCES
        or PLACEMENT.fullmatch(placement) is None
    ):
        raise ValueError("Campaign needs a transaction and bounded source/placement")
    if type(published_on) is not date:
        raise ValueError("Campaign publication needs a calendar date")
    campaign = AcquisitionCampaign(
        id=uuid4(),
        code="c_" + secrets.token_urlsafe(16),
        source=source,
        placement=placement,
        published_on=published_on,
        created_at=at,
    )
    session.add(campaign)
    await session.flush()
    return campaign


async def classify_start(
    session: AsyncSession,
    payload: str | None,
    *,
    at: datetime,
) -> tuple[TouchKind, UUID | None]:
    require_utc(at)
    if not session.in_transaction():
        raise ValueError("Start source recognition needs a transaction")
    if payload is None or payload == "":
        return TouchKind.DIRECT, None
    if CODE.fullmatch(payload) is None:
        return TouchKind.UNKNOWN, None
    query = select(AcquisitionCampaign.id).where(AcquisitionCampaign.code == payload)
    campaign_id = await session.scalar(query)
    if campaign_id is None:
        # The URL is sufficient: first accepted use creates its exact, case-sensitive
        # code. Concurrent first arrivals share one row; existing curated metadata
        # stays immutable. Publication date is unknown, so use first observation.
        await session.execute(
            insert(AcquisitionCampaign)
            .values(
                id=uuid5(NAMESPACE_URL, f"goaletto:acquisition-code:v1:{payload}"),
                code=payload,
                source="other",
                placement=payload.lower(),
                published_on=at.date(),
                created_at=at,
            )
            .on_conflict_do_nothing(index_elements=["code"])
        )
        campaign_id = await session.scalar(query)
        if campaign_id is None:
            raise ValueError("Start source was not persisted")
    return TouchKind.KNOWN, campaign_id


@dataclass(frozen=True)
class StartResult:
    manager_id: UUID
    analytics_manager_id: UUID | None
    operation_id: UUID
    kind: TouchKind
    campaign_id: UUID | None
    created_touch: bool


async def accept_start(
    session: AsyncSession,
    *,
    telegram_id: int,
    update_id: int,
    payload: str | None,
    at: datetime,
    locale: str = "ru",
    internal: bool = False,
) -> StartResult:
    require_utc(at)
    if not session.in_transaction():
        raise ValueError("Start attribution needs a transaction")
    if type(telegram_id) is not int or not 0 < telegram_id < 2**63 or locale not in {"ru", "en"}:
        raise ValueError("Invalid manager identity or locale")
    operation = telegram_operation(update_id)
    await session.execute(
        insert(Manager)
        .values(
            id=uuid4(),
            telegram_id=telegram_id,
            locale=locale,
            is_internal=internal,
            created_at=at,
            first_game_start_at=None,
        )
        .on_conflict_do_nothing(index_elements=["telegram_id"])
    )
    manager = await session.scalar(
        select(Manager).where(Manager.telegram_id == telegram_id).with_for_update()
    )
    if manager is None:
        raise ValueError("Manager was not persisted")
    if internal:
        manager.is_internal = True
    analytics_id = await pseudonym_for(session, manager.id, at)
    if analytics_id is None:
        # Internal/opted-out accounts can still play, but contribute no attribution/cohort.
        return StartResult(manager.id, None, operation, TouchKind.UNKNOWN, None, False)
    previous = await session.get(AcquisitionTouch, operation)
    if previous is not None:
        if previous.analytics_manager_id != analytics_id:
            raise ValueError("Update identity belongs to a different manager")
        return StartResult(
            manager.id,
            analytics_id,
            operation,
            TouchKind(previous.kind),
            previous.campaign_id,
            False,
        )
    kind, campaign_id = await classify_start(session, payload, at=at)
    first = await session.get(AcquisitionFirstTouch, analytics_id)
    first_start = manager.first_game_start_at is None
    if first is None and first_start:
        session.add(
            AcquisitionFirstTouch(
                analytics_manager_id=analytics_id,
                operation_id=operation,
                kind=kind.value,
                campaign_id=campaign_id,
                started_at=at,
            )
        )
    session.add(
        AcquisitionTouch(
            operation_id=operation,
            analytics_manager_id=analytics_id,
            kind=kind.value,
            campaign_id=campaign_id,
            occurred_at=at,
            recorded_at=at,
        )
    )
    await session.flush()
    for effect, name in ((0, "manager_started"), (1, "campaign_touched"), (2, "start_accepted")):
        if (
            name == "manager_started"
            and not first_start
            or name == "campaign_touched"
            and campaign_id is None
        ):
            continue
        await record_event(
            session,
            JourneyEvent(
                event_id=uuid5(operation, f"event:{effect}"),
                name=name,
                occurred_at=at,
                recorded_at=at,
                origin=Origin.USER,
                operation_id=operation,
                effect_number=effect,
                surface=Surface.ONBOARDING,
                analytics_manager_id=analytics_id,
                session_id=uuid5(operation, "session-proposal"),
                campaign_id=campaign_id,
            ),
        )
    return StartResult(manager.id, analytics_id, operation, kind, campaign_id, True)
