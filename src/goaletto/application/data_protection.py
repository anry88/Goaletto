"""A durable production latch. Development reset permission ends with real ownership."""

from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.infrastructure.models import DataProtection


async def protect(session: AsyncSession, at: datetime) -> None:
    await session.execute(
        insert(DataProtection)
        .values(id=1, protected_at=at)
        .on_conflict_do_update(
            index_elements=["id"],
            set_={"protected_at": at},
            where=DataProtection.protected_at.is_(None),
        )
    )
