"""Persist season terms once; settle historical intervals without repricing."""

from datetime import datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.application.game_actions import allocation_lock
from goaletto.domain.sponsorship import POLICY, sponsor_accrual, sponsor_terms
from goaletto.infrastructure.models import Club, FinanceCycle, SponsorContract


async def sign_sponsor(
    session: AsyncSession,
    club: Club,
    season_id: UUID,
    at: datetime,
    review_at: datetime,
    *,
    tier: int,
    place: int | None = None,
    source_season_id: UUID | None = None,
) -> SponsorContract:
    require_utc(at)
    require_utc(review_at)
    await allocation_lock(session)
    previous = await session.scalar(
        select(SponsorContract).where(
            SponsorContract.club_id == club.id, SponsorContract.season_id == season_id
        )
    )
    if previous is not None:
        return previous
    if await session.scalar(
        select(FinanceCycle.id)
        .where(FinanceCycle.club_id == club.id, FinanceCycle.week_start > at)
        .limit(1)
    ):
        raise ValueError("Cannot backdate sponsorship into a settled period")
    active = await session.scalar(
        select(SponsorContract)
        .where(SponsorContract.club_id == club.id, SponsorContract.ends_at.is_(None))
        .with_for_update()
    )
    if active:
        if at < active.starts_at:
            raise ValueError("Sponsorship dates cannot move backwards")
        active.ends_at = at
        await session.flush()
    terms = sponsor_terms(tier, place, club.supporters)
    row = SponsorContract(
        id=uuid5(club.id, f"{POLICY}:{season_id}"),
        club_id=club.id,
        season_id=season_id,
        source_season_id=source_season_id,
        basis="season_result" if source_season_id else "opening",
        policy_version=POLICY,
        tier=terms.tier,
        place=terms.place,
        supporters=terms.supporters,
        cycle_rate=terms.cycle_rate,
        starts_at=at,
        review_at=review_at,
    )
    session.add(row)
    await session.flush()
    return row


def microseconds(delta: timedelta) -> int:
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


async def sponsor_for_period(
    session: AsyncSession, club_id: UUID, start: datetime, end: datetime
) -> tuple[int, list[dict[str, object]]]:
    require_utc(start)
    require_utc(end)
    rows = (
        await session.scalars(
            select(SponsorContract)
            .where(
                SponsorContract.club_id == club_id,
                SponsorContract.starts_at < end,
                (SponsorContract.ends_at.is_(None)) | (SponsorContract.ends_at > start),
            )
            .order_by(SponsorContract.starts_at)
        )
    ).all()
    cursor, total, snapshots = start, 0, []
    for contract in rows:
        if contract.policy_version != POLICY:
            raise ValueError("Unknown sponsor policy")
        left, right = max(start, contract.starts_at), min(end, contract.ends_at or end)
        if left != cursor:
            raise ValueError("Sponsorship interval has a gap or overlap")
        due = sponsor_accrual(
            contract.cycle_rate,
            microseconds(left - contract.starts_at),
            microseconds(right - contract.starts_at),
        )
        snapshots.append(
            {
                "id": str(contract.id),
                "season_id": str(contract.season_id),
                "policy": contract.policy_version,
                "tier": contract.tier,
                "place": contract.place,
                "supporters": contract.supporters,
                "rate": contract.cycle_rate,
                "due": due,
                "from": left.isoformat(),
                "until": right.isoformat(),
            }
        )
        total += due
        cursor = right
    if cursor != end:
        raise ValueError("Missing sponsorship interval")
    return total, snapshots
