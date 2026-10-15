"""Lazy durable recovery under the shared allocation lock; explicit application time."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.application.game_actions import allocation_lock
from goaletto.domain.match_codec import restore_state
from goaletto.domain.match_types import MatchState
from goaletto.domain.recovery import daily_recovery, ready_in, recover
from goaletto.infrastructure.models import Match, Person, Player, PlayerRecovery, SalaryContract


@dataclass(frozen=True)
class EnergyStatus:
    bps: int
    full_at: datetime | None


async def recover_player(session: AsyncSession, person_id: UUID, at: datetime) -> PlayerRecovery:
    require_utc(at)
    at = at.replace(microsecond=0)
    await allocation_lock(session)
    player = await session.get(Player, person_id)
    person = await session.get(Person, person_id)
    if player is None or person is None:
        raise ValueError("Missing recovery player")
    row = await session.get(PlayerRecovery, person_id, with_for_update=True)
    if row is None:
        row = PlayerRecovery(
            person_id=person_id,
            energy_bps=10000,
            remainder=0,
            recovered_at=at,
            stamina=player.stamina,
            age=person.age,
            policy_version="recovery-v1",
        )
        session.add(row)
        await session.flush()
        return row
    if row.active_match_id is not None or at < row.recovered_at:
        return row
    # Player employment determines the club in every interval, including a transfer.
    contracts = list(
        (
            await session.scalars(
                select(SalaryContract).where(
                    SalaryContract.person_id == person_id,
                    SalaryContract.role == "player",
                    SalaryContract.started_at < at,
                    (SalaryContract.ended_at.is_(None))
                    | (SalaryContract.ended_at > row.recovered_at),
                )
            )
        ).all()
    )
    clubs = {c.club_id for c in contracts}
    physios = (
        list(
            (
                await session.scalars(
                    select(SalaryContract).where(
                        SalaryContract.club_id.in_(clubs),
                        SalaryContract.role == "physiotherapist",
                        SalaryContract.started_at < at,
                        (SalaryContract.ended_at.is_(None))
                        | (SalaryContract.ended_at > row.recovered_at),
                    )
                )
            ).all()
        )
        if clubs
        else []
    )
    boundaries = {row.recovered_at, at}
    for contract in (*contracts, *physios):
        for point in (contract.started_at, contract.ended_at):
            if point is not None and row.recovered_at < point < at:
                boundaries.add(point)
    points = sorted(boundaries)
    for start, end in zip(points, points[1:], strict=False):
        club = next(
            (
                c.club_id
                for c in contracts
                if c.started_at <= start and (c.ended_at is None or c.ended_at > start)
            ),
            None,
        )
        skill = max(
            (
                c.recovery_skill
                for c in physios
                if c.club_id == club
                and c.started_at <= start
                and (c.ended_at is None or c.ended_at > start)
            ),
            default=0,
        )
        row.energy_bps, row.remainder = recover(
            row.energy_bps,
            row.remainder,
            int((end - start).total_seconds()),
            daily_recovery(row.stamina, row.age, skill),
        )
    row.recovered_at, row.stamina, row.age = at, player.stamina, person.age
    return row


async def energy_status(session: AsyncSession, person_id: UUID, at: datetime) -> EnergyStatus:
    row = await recover_player(session, person_id, at)
    if row.active_match_id is not None:
        match = await session.get(Match, row.active_match_id)
        if match is None:
            raise ValueError("Missing active energy match")
        state = restore_state(match.state)
        condition = next(
            c
            for team in (state.home, state.away)
            for c in team.conditions
            if c.player_id == person_id
        )
        return EnergyStatus(condition.energy_bps, None)
    player = await session.get(Player, person_id)
    if player is None:
        raise ValueError("Missing recovery player")
    skill = await session.scalar(
        select(SalaryContract.recovery_skill)
        .where(
            SalaryContract.club_id == player.club_id,
            SalaryContract.role == "physiotherapist",
            SalaryContract.started_at <= at,
            (SalaryContract.ended_at.is_(None)) | (SalaryContract.ended_at > at),
        )
        .order_by(SalaryContract.recovery_skill.desc())
        .limit(1)
    )
    rate = daily_recovery(row.stamina, row.age, skill or 0)
    return EnergyStatus(
        row.energy_bps,
        max(at, row.recovered_at)
        + timedelta(seconds=ready_in(row.energy_bps, row.remainder, rate)),
    )


async def lock_energy(session: AsyncSession, match: Match, state: MatchState, at: datetime) -> None:
    for team in (state.home, state.away):
        for person in team.snapshot.players:
            row = await recover_player(session, person.id, at)
            if row.active_match_id not in (None, match.id):
                from goaletto.application.match_runtime import NotReady

                raise NotReady("Player is already committed to another match")
            row.active_match_id = match.id


async def finish_energy(session: AsyncSession, state: MatchState, at: datetime) -> None:
    conditions = [c for team in (state.home, state.away) for c in team.conditions]
    rows = {
        r.person_id: r
        for r in (
            await session.scalars(
                select(PlayerRecovery)
                .where(PlayerRecovery.person_id.in_([c.player_id for c in conditions]))
                .order_by(PlayerRecovery.person_id)
                .with_for_update()
            )
        ).all()
    }
    for condition in conditions:
        row = rows.get(condition.player_id)
        if row is None or row.active_match_id != state.match_id:
            raise ValueError("Missing frozen match energy")
        row.energy_bps, row.remainder = condition.energy_bps, 0
        row.recovered_at, row.active_match_id = at.replace(microsecond=0), None
