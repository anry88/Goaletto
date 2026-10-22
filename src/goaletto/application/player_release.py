"""Confirmed player release: one cycle compensation, same person in global supply."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface, require_utc
from goaletto.application.finances import close_salary
from goaletto.application.game_actions import Actor, PermissionDenied, owned_club, user_effect
from goaletto.application.market import player_locked, repair_lineup, seller_safe
from goaletto.application.money import debit_locked, locked_accounts
from goaletto.domain.finances import POLICY_VERSION, player_fee, player_wage
from goaletto.domain.world import SKILL_NAMES
from goaletto.infrastructure.models import (
    Auction,
    CandidateOffer,
    CandidatePool,
    Fixture,
    Player,
    PlayerRelease,
    SalaryContract,
)


async def eligible_player(
    session: AsyncSession, club_id: UUID, person_id: UUID, at: datetime
) -> tuple[Player, SalaryContract]:
    player, person = await player_locked(session, person_id)
    if player.club_id != club_id or player.status != "active" or person.career != "player":
        raise ValueError("Player left or is playing their last season")
    if await session.scalar(
        select(Auction.id).where(Auction.person_id == person_id, Auction.status == "open")
    ):
        raise ValueError("Withdraw the auction before releasing the player")
    if await session.scalar(
        select(Fixture.id)
        .where(
            (Fixture.home_club_id == club_id) | (Fixture.away_club_id == club_id),
            Fixture.status.in_(("scheduled", "live", "paused")),
            Fixture.lineup_lock_at <= at,
            Fixture.actual_finish_at.is_(None),
        )
        .limit(1)
    ):
        raise ValueError("Wait until the locked match finishes")
    if not await seller_safe(session, club_id, player):
        raise ValueError("Keep at least 16 players and one goalkeeper, including listed players")
    salary = await session.scalar(
        select(SalaryContract)
        .where(
            SalaryContract.person_id == person_id,
            SalaryContract.club_id == club_id,
            SalaryContract.ended_at.is_(None),
            SalaryContract.role == "player",
        )
        .with_for_update()
    )
    if salary is None or at < salary.started_at:
        raise ValueError("Player contract unavailable")
    return player, salary


async def quote_release(
    session: AsyncSession,
    actor: Actor,
    ownership_id: UUID,
    person_id: UUID,
    *,
    clock: Callable[[], datetime] | None = None,
) -> PlayerRelease:
    owner, club = await owned_club(session, actor)
    actor = replace(actor, at=max(actor.at, clock() if clock else datetime.now(UTC)))
    if owner.expires_at <= actor.at:
        raise PermissionDenied("Ownership expired")
    if owner.id != ownership_id:
        raise PermissionDenied("Previous ownership")
    key = uuid5(actor.operation_id, "player-release:v1")
    previous = await session.get(PlayerRelease, key)
    if previous:
        if (previous.person_id, previous.ownership_id) != (person_id, owner.id):
            raise PermissionDenied("Release operation reused")
        return previous
    _, salary = await eligible_player(session, club.id, person_id, actor.at)
    from goaletto.infrastructure.models import Person

    person = await session.get(Person, person_id)
    assert person is not None
    # Legacy wages cover seven days; the approved compensation covers four.
    compensation = (
        salary.weekly_rate
        if salary.policy_version == POLICY_VERSION
        else (salary.weekly_rate * 4 + 6) // 7
    )
    row = PlayerRelease(
        id=key,
        person_id=person_id,
        club_id=club.id,
        ownership_id=owner.id,
        salary_id=salary.id,
        compensation=compensation,
        name=person.name,
        created_at=actor.at,
    )
    session.add(row)
    await user_effect(
        session, actor, "market_opened", Surface.CLUB, ownership=owner, club_id=club.id
    )
    await session.flush()
    return row


async def confirm_release(
    session: AsyncSession,
    actor: Actor,
    release_id: UUID,
    *,
    clock: Callable[[], datetime] | None = None,
) -> PlayerRelease:
    require_utc(actor.at)
    owner, club = await owned_club(session, actor)
    actor = replace(actor, at=max(actor.at, clock() if clock else datetime.now(UTC)))
    if owner.expires_at <= actor.at:
        raise PermissionDenied("Ownership expired")
    row = await session.get(PlayerRelease, release_id, with_for_update=True)
    if row is None or (row.ownership_id, row.club_id) != (owner.id, club.id):
        raise PermissionDenied("Release belongs to another ownership")
    if row.confirmed_at is not None:
        return row
    if not row.created_at <= actor.at < row.created_at + timedelta(minutes=15):
        raise ValueError("Confirmation expired; open the player card again")
    player, salary = await eligible_player(session, club.id, row.person_id, actor.at)
    if salary.id != row.salary_id:
        raise ValueError("Salary contract changed")
    from goaletto.application.candidates import pool_calendar
    from goaletto.infrastructure.models import Person

    person = await session.get(Person, row.person_id)
    assert person is not None
    account = (await locked_accounts(session, club.id))[club.id]
    if row.compensation:
        await debit_locked(
            session,
            account,
            f"player-release:{row.id}",
            "player_release",
            row.compensation,
            actor.at,
            details={"person_id": str(row.person_id), "salary_id": str(salary.id)},
        )
    await repair_lineup(session, club.id, player.person_id, None, actor.at)
    from goaletto.application.recovery import recover_player

    condition = await recover_player(session, person.id, actor.at)
    if condition.active_match_id is not None:
        raise ValueError("Player still belongs to an unfinished match")
    await close_salary(session, person.id, actor.at)
    player.club_id = None
    season = await pool_calendar(session)
    await session.execute(
        insert(CandidatePool)
        .values(
            kind="player",
            profile="player",
            target=12,
            generation=0,
            home_league_set_id=season.league_set_id,
        )
        .on_conflict_do_nothing()
    )
    skills = tuple(getattr(player, k) for k in SKILL_NAMES[:8])
    count = 1 + len(player.alternative_positions)
    fee, wage = (
        player_fee(skills, person.age, player.position, count),
        player_wage(skills, person.age, player.position, count),
    )
    offer = await session.get(CandidateOffer, person.id, with_for_update=True)
    if offer is None:
        offer = CandidateOffer(
            person_id=person.id,
            kind="player",
            pool_profile="player",
            profile=player.position,
            fee=fee,
            weekly_rate=wage,
            economy_policy=POLICY_VERSION,
            status="available",
            created_at=actor.at,
        )
        session.add(offer)
    else:
        if offer.kind != "player" or offer.status != "signed":
            raise ValueError("Conflicting player offer")
        offer.fee, offer.weekly_rate, offer.created_at = fee, wage, actor.at
        offer.status, offer.profile, offer.pool_profile, offer.economy_policy = (
            "available",
            player.position,
            "player",
            POLICY_VERSION,
        )
    row.confirmed_at = actor.at
    await user_effect(
        session, actor, "player_contract_released", Surface.CLUB, ownership=owner, club_id=club.id
    )
    await session.flush()
    return row
