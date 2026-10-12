"""Durable Telegram portrait file cache.

New table only; existing models, chain history and worker wiring are untouched.
Until the migration is agreed and applied, bot code uses the in-memory store
in ``goaletto.bot.portrait_cache`` behind the same ``PortraitFileStore``
protocol. Offline SQL rendering of migration 0010 covers DDL only and does
not prove live PostgreSQL races, rollback or trigger behavior.
"""

from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.infrastructure.models import TelegramPortraitFile


async def load_file_id(session: AsyncSession, cache_key: str) -> str | None:
    if type(cache_key) is not str or len(cache_key) != 64:
        raise ValueError("Invalid portrait cache key")
    row = await session.get(TelegramPortraitFile, cache_key)
    return row.file_id if row is not None else None


async def store_file_id(
    session: AsyncSession,
    *,
    cache_key: str,
    identity_id: str,
    variant_id: str,
    catalog_version: int,
    kit_primary: str,
    kit_secondary: str,
    kit_version: int,
    size: int,
    background: str,
    file_id: str,
    at: datetime,
) -> None:
    from goaletto.application.analytics import require_utc

    require_utc(at)
    if type(cache_key) is not str or len(cache_key) != 64:
        raise ValueError("Invalid portrait cache key")
    if type(file_id) is not str or not 1 <= len(file_id) <= 500:
        raise ValueError("Invalid Telegram file_id")
    values = {
        "cache_key": cache_key,
        "identity_id": identity_id,
        "variant_id": variant_id,
        "catalog_version": catalog_version,
        "kit_primary": kit_primary,
        "kit_secondary": kit_secondary,
        "kit_version": kit_version,
        "size": size,
        "background": background,
        "file_id": file_id,
        "created_at": at,
        "updated_at": at,
    }
    await session.execute(
        insert(TelegramPortraitFile)
        .values(values)
        .on_conflict_do_update(
            index_elements=["cache_key"],
            set_={"file_id": file_id, "updated_at": at},
        )
    )
