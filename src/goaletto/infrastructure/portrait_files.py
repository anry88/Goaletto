"""Durable Telegram portrait file cache. PROPOSAL for Codex review.

New table only; existing models, chain history and worker wiring are untouched.
Until the migration is agreed and applied, bot code uses the in-memory store
in ``goaletto.bot.portrait_cache`` behind the same ``PortraitFileStore``
protocol. Offline SQL rendering of migration 0010 covers DDL only and does
not prove live PostgreSQL races, rollback or trigger behavior.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from goaletto.infrastructure.models import Base


class TelegramPortraitFile(Base):
    __tablename__ = "telegram_portrait_files"
    __table_args__ = (
        CheckConstraint("length(cache_key) = 64", name="ck_portrait_file_key"),
        CheckConstraint("catalog_version > 0", name="ck_portrait_file_catalog"),
        CheckConstraint("kit_version > 0", name="ck_portrait_file_kit_version"),
        CheckConstraint("size IN (96, 256)", name="ck_portrait_file_size"),
        CheckConstraint("length(file_id) BETWEEN 1 AND 500", name="ck_portrait_file_id"),
    )
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    identity_id: Mapped[str] = mapped_column(String(100))
    variant_id: Mapped[str] = mapped_column(String(100))
    catalog_version: Mapped[int] = mapped_column(Integer)
    kit_primary: Mapped[str] = mapped_column(String(7))
    kit_secondary: Mapped[str] = mapped_column(String(7))
    kit_version: Mapped[int] = mapped_column(Integer)
    size: Mapped[int] = mapped_column(Integer)
    background: Mapped[str] = mapped_column(String(32), default="transparent")
    file_id: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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
