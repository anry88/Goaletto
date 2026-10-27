"""Audited, one-time English names for clubs which have never had an owner."""

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.application.game_actions import allocation_lock
from goaletto.config import Settings
from goaletto.domain.club_name_catalog import (
    CLUB_NAMING_POLICY,
    ENGLISH_CLUB_NAMES,
    allocate_names,
    name_key,
)
from goaletto.infrastructure.database import Database
from goaletto.infrastructure.models import Club, DefaultClubNameReceipt, OwnershipPeriod


@dataclass(frozen=True)
class NameChange:
    club_id: UUID
    before: str
    after: str


async def update_default_names(
    session: AsyncSession, *, at: datetime, apply: bool = False
) -> tuple[NameChange, ...]:
    """Caller owns the transaction; claim, rename and world generation share this lock."""
    require_utc(at)
    if not session.in_transaction():
        raise ValueError("Club naming requires a caller-owned transaction")
    await allocation_lock(session)
    # Any historical ownership excludes the club, even if it is free again.
    ever_owned = select(OwnershipPeriod.id).where(OwnershipPeriod.club_id == Club.id).exists()
    updated = (
        select(DefaultClubNameReceipt.club_id)
        .where(DefaultClubNameReceipt.club_id == Club.id)
        .exists()
    )
    clubs = (
        await session.scalars(
            select(Club)
            .where(Club.owner_telegram_id.is_(None), ~ever_owned, ~updated)
            .order_by(Club.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).all()
    english = {name_key(name) for name in ENGLISH_CLUB_NAMES}
    targets = [club for club in clubs if name_key(club.name) not in english]
    target_ids = {club.id for club in targets}
    existing = (await session.execute(select(Club.id, Club.name))).all()
    names = allocate_names(
        len(targets),
        seed="existing-never-owned",
        existing=(name for club_id, name in existing if club_id not in target_ids),
    )
    changes = []
    for club, name in zip(targets, names, strict=True):
        changes.append(NameChange(club.id, club.name, name))
        if apply:
            session.add(
                DefaultClubNameReceipt(
                    club_id=club.id,
                    policy_version=CLUB_NAMING_POLICY,
                    before_name=club.name,
                    after_name=name,
                    applied_at=at,
                )
            )
            club.name = name
    if apply:
        await session.flush()
    return tuple(changes)


async def run(*, apply: bool) -> dict[str, object]:
    database = Database(Settings())
    try:
        async with database.transaction() as session:
            changes = await update_default_names(session, at=datetime.now(UTC), apply=apply)
        return {
            "policy": CLUB_NAMING_POLICY,
            "applied": apply,
            "count": len(changes),
            "changes": [asdict(change) for change in changes],
        }
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Persist the previewed name changes")
    args = parser.parse_args()
    try:
        result = asyncio.run(run(apply=args.apply))
    except Exception as error:
        raise SystemExit(f"Club naming failed ({type(error).__name__})") from None
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
