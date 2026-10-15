"""Four-day accounts, immutable wage contracts, match income and owned finance reads."""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface, require_utc
from goaletto.application.finance_seed import cycle_job
from goaletto.application.game_actions import Actor, allocation_lock, owned_club, user_effect
from goaletto.application.match_runtime import NotReady
from goaletto.application.money import credit_locked, debit_locked, locked_accounts
from goaletto.domain.finances import (
    POLICY_VERSION,
    accrued_wage,
    merchandise_income,
    player_wage,
    season_prize,
    sponsor_income,
    stadium_upkeep,
    staff_wage,
    ticket_income,
)
from goaletto.domain.game_cycle import CYCLE_SECONDS
from goaletto.domain.match_types import MatchState
from goaletto.domain.standings import LeagueStanding, add_result, rank_league
from goaletto.domain.world import SKILL_NAMES
from goaletto.infrastructure.models import (
    FinanceCycle,
    Fixture,
    Job,
    League,
    LedgerEntry,
    MatchResult,
    Person,
    Player,
    SalaryContract,
    Season,
    SeasonClub,
    Staff,
    StaffQualification,
)


async def salary_contract(
    session: AsyncSession,
    person_id: UUID,
    club_id: UUID,
    role: str,
    at: datetime,
    *,
    quoted_player_wage: int | None = None,
) -> SalaryContract:
    """After a hire/transfer, bind a fixed wage. Caller owns the full transaction."""
    require_utc(at)
    await allocation_lock(session)
    person = await session.scalar(select(Person).where(Person.id == person_id).with_for_update())
    row = await session.scalar(
        select(SalaryContract)
        .where(SalaryContract.person_id == person_id, SalaryContract.ended_at.is_(None))
        .with_for_update()
    )
    if person is None:
        raise ValueError("Missing salary person")
    worker: Player | Staff | None
    if role == "player":
        worker = await session.get(Player, person_id)
    else:
        worker = await session.get(Staff, person_id)
    if (
        worker is None
        or worker.club_id != club_id
        or worker.status not in {"active", "last_season"}
    ):
        raise ValueError("Salary requires current employment")
    if row is not None:
        if (
            row.club_id != club_id
            or row.role != role
            or quoted_player_wage is not None
            and row.weekly_rate != quoted_player_wage
        ):
            raise ValueError("Close previous salary before changing employer")
        return row
    if await session.scalar(
        select(SalaryContract.id)
        .where(SalaryContract.person_id == person_id, SalaryContract.ended_at > at)
        .limit(1)
    ):
        raise ValueError("Salary contracts cannot overlap")
    if isinstance(worker, Staff) and worker.role != role:
        raise ValueError("Salary role differs from employment")
    if await session.scalar(
        select(FinanceCycle.id)
        .where(FinanceCycle.club_id == club_id, FinanceCycle.week_start > at)
        .limit(1)
    ):
        raise ValueError("Cannot backdate salary into a settled period")
    rate = (
        player_wage(tuple(getattr(worker, name) for name in SKILL_NAMES[:8]))
        if role == "player"
        else staff_wage(role)
    )
    if quoted_player_wage is not None:
        if (
            role != "player"
            or type(quoted_player_wage) is not int
            or not 180 <= quoted_player_wage <= 1700
        ):
            raise ValueError("Invalid agreed player wage")
        rate = quoted_player_wage
    recovery_skill = 0
    if role == "physiotherapist":
        qualification = await session.get(StaffQualification, (person_id, role))
        if qualification is None:
            raise ValueError("Physiotherapist needs an explicit qualification")
        recovery_skill = qualification.skill
    row = SalaryContract(
        id=uuid5(person_id, f"salary:v1:{club_id}:{at.isoformat()}"),
        person_id=person_id,
        club_id=club_id,
        role=role,
        weekly_rate=rate,
        recovery_skill=recovery_skill,
        policy_version=POLICY_VERSION,
        started_at=at,
    )
    session.add(row)
    await session.flush()
    return row


async def close_salary(session: AsyncSession, person_id: UUID, at: datetime) -> None:
    require_utc(at)
    await allocation_lock(session)
    await session.scalar(select(Person).where(Person.id == person_id).with_for_update())
    row = await session.scalar(
        select(SalaryContract)
        .where(SalaryContract.person_id == person_id, SalaryContract.ended_at.is_(None))
        .with_for_update()
    )
    if row is None:
        return
    if at < row.started_at or await session.scalar(
        select(FinanceCycle.id)
        .where(FinanceCycle.club_id == row.club_id, FinanceCycle.week_start > at)
        .limit(1)
    ):
        raise ValueError("Cannot backdate salary close")
    row.ended_at = at
    await session.flush()


async def ranking_at(
    session: AsyncSession, club_id: UUID, at: datetime, *, season_id: UUID | None = None
) -> tuple[UUID, int, int]:
    # Historical results only: a delayed invoice cannot use future league points.
    memberships = (
        await session.execute(
            select(SeasonClub, Season, League.tier)
            .join(Season, Season.id == SeasonClub.season_id)
            .join(League, League.id == SeasonClub.league_id)
            .where(
                SeasonClub.club_id == club_id,
                SeasonClub.season_id == season_id
                if season_id is not None
                else SeasonClub.club_id == club_id,
            )
            .order_by(Season.starts_at.desc())
        )
    ).all()
    if not memberships:
        raise ValueError("Club has no league membership")
    member, season, tier = next(
        (row for row in memberships if row[1].starts_at <= at), memberships[-1]
    )
    members = (
        await session.scalars(
            select(SeasonClub).where(
                SeasonClub.season_id == season.id, SeasonClub.league_id == member.league_id
            )
        )
    ).all()
    rows = {m.club_id: LeagueStanding(m.club_id, "Club", m.tie_break_order) for m in members}
    finished = (
        await session.scalars(
            select(MatchResult)
            .where(
                MatchResult.season_id == season.id,
                MatchResult.league_id == member.league_id,
                MatchResult.actual_finish_at < at,
            )
            .order_by(MatchResult.actual_finish_at, MatchResult.match_id)
        )
    ).all()
    for result in finished:
        rows[result.home_club_id] = add_result(
            rows[result.home_club_id], result.home_goals, result.away_goals
        )
        rows[result.away_club_id] = add_result(
            rows[result.away_club_id], result.away_goals, result.home_goals
        )
    ranked = rank_league(tuple(rows.values()))
    return season.id, tier, next(i for i, row in enumerate(ranked, 1) if row.club_id == club_id)


async def cycle_finances(session: AsyncSession, job: Job, at: datetime) -> None:
    require_utc(at)
    p = job.payload
    if (
        job.kind != "finance.cycle"
        or set(p) != {"version", "club_id", "period_start", "period_end", "policy"}
        or p["version"] != 2
        or p["policy"] != "economy-cycle-v1"
    ):
        raise ValueError("Invalid cycle finance job")
    club_id = UUID(p["club_id"])
    start, week = datetime.fromisoformat(p["period_start"]), datetime.fromisoformat(p["period_end"])
    require_utc(start)
    require_utc(week)
    if not timedelta(0) < week - start <= timedelta(seconds=CYCLE_SECONDS) or job.due_at != week:
        raise ValueError("Invalid finance cycle")
    period_seconds, policy = CYCLE_SECONDS, "economy-cycle-v1"
    if at < week:
        raise NotReady("Finance period not finished")
    if not await allocation_lock(session, try_lock=True):
        raise NotReady("Busy finance account")
    account = (await locked_accounts(session, club_id))[club_id]
    cycle_id = uuid5(club_id, "finance:cycle-v1:" + week.isoformat())
    prior = await session.get(FinanceCycle, cycle_id)
    if prior is None:
        # Settle in chronological order; never silently skip a missing invoice.
        older = await session.scalar(
            select(Job.id)
            .where(
                Job.kind == "finance.cycle",
                Job.payload["club_id"].astext == str(club_id),
                Job.due_at < week,
                Job.status != "done",
            )
            .limit(1)
        )
        if older is not None:
            raise NotReady("Earlier invoice must settle first")
        contracts = (
            await session.scalars(
                select(SalaryContract)
                .where(
                    SalaryContract.club_id == club_id,
                    SalaryContract.started_at < week,
                    (SalaryContract.ended_at.is_(None)) | (SalaryContract.ended_at > start),
                )
                .order_by(SalaryContract.id)
            )
        ).all()
        wages = {"player_salary": 0, "staff_salary": 0}
        contract_snapshot = []
        for contract in contracts:
            if contract.policy_version != POLICY_VERSION:
                raise ValueError("Unsupported salary policy")
            seconds = math.ceil(
                (
                    min(week, contract.ended_at or week) - max(start, contract.started_at)
                ).total_seconds()
            )
            due = accrued_wage(contract.weekly_rate, seconds, CYCLE_SECONDS)
            wages["player_salary" if contract.role == "player" else "staff_salary"] += due
            contract_snapshot.append(
                {
                    "id": str(contract.id),
                    "seconds": seconds,
                    "rate": contract.weekly_rate,
                    "period_seconds": CYCLE_SECONDS,
                    "due": due,
                }
            )
        season_id, tier, place = await ranking_at(session, club_id, week)
        from goaletto.application.stadium import upkeep_for_period

        upkeep, capacity_periods = await upkeep_for_period(
            session, account.club, week, period_start=start
        )
        amounts = {
            "sponsor": accrued_wage(
                sponsor_income(tier, place), int((week - start).total_seconds()), period_seconds
            ),
            "merchandise": accrued_wage(
                merchandise_income(account.club.supporters),
                int((week - start).total_seconds()),
                period_seconds,
            ),
            **wages,
            "stadium_upkeep": upkeep,
        }
        key = "cycle:" + week.isoformat()
        details = {
            "period_start": start.isoformat(),
            "period_end": week.isoformat(),
            "period_seconds": period_seconds,
            "policy": policy,
        }
        for kind in ("sponsor", "merchandise"):
            if amounts[kind]:
                await credit_locked(
                    session, account, key + ":" + kind, kind, amounts[kind], at, details
                )
        for kind in ("player_salary", "staff_salary", "stadium_upkeep"):
            if amounts[kind]:
                await debit_locked(
                    session,
                    account,
                    key + ":" + kind,
                    kind,
                    amounts[kind],
                    at,
                    mandatory=True,
                    details=details,
                )
        session.add(
            FinanceCycle(
                id=cycle_id,
                club_id=club_id,
                week_start=week,
                applied_at=at,
                policy_version=policy,
                snapshot={
                    "amounts": amounts,
                    "contracts": contract_snapshot,
                    "capacity_periods": capacity_periods,
                    "season_id": str(season_id),
                    "tier": tier,
                    "place": place,
                    "supporters": account.club.supporters,
                    "capacity": capacity_periods[-1]["capacity"]
                    if capacity_periods
                    else account.club.stadium_capacity,
                },
            )
        )
        await session.flush()
    elif prior.week_start != week or prior.policy_version != policy:
        raise ValueError("Conflicting finance cycle")
    await session.execute(
        insert(Job)
        .values(**cycle_job(club_id, week))
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )


async def match_income(
    session: AsyncSession, fixture: Fixture, state: MatchState, at: datetime
) -> None:
    account = (await locked_accounts(session, fixture.home_club_id))[fixture.home_club_id]
    amount = ticket_income(state.crowd.home_fans, state.crowd.stadium_capacity)
    if amount:
        await credit_locked(
            session,
            account,
            f"tickets:{fixture.id}",
            "tickets",
            amount,
            at,
            {
                "fixture_id": str(fixture.id),
                "attendance": state.crowd.home_fans,
                "capacity": state.crowd.stadium_capacity,
                "policy": POLICY_VERSION,
            },
        )
    await pay_league_prizes(session, fixture.season_id, fixture.league_id, at)


async def pay_league_prizes(
    session: AsyncSession, season_id: UUID, league_id: UUID, at: datetime
) -> bool:
    from goaletto.application.results import league_table

    require_utc(at)
    await allocation_lock(session)
    fixtures = (
        await session.scalars(
            select(Fixture).where(Fixture.season_id == season_id, Fixture.league_id == league_id)
        )
    ).all()
    count = await session.scalar(
        select(func.count())
        .select_from(MatchResult)
        .where(MatchResult.season_id == season_id, MatchResult.league_id == league_id)
    )
    if len(fixtures) != 90 or count != 90 or any(f.status != "finished" for f in fixtures):
        return False
    if any(f.actual_finish_at is None or f.actual_finish_at > at for f in fixtures):
        raise ValueError("Prize date precedes completed fixtures")
    ranked = await league_table(session, season_id, league_id)
    if len(ranked) != 10 or any(row.played != 18 for row in ranked):
        raise ValueError("Final table incomplete")
    tier = await session.scalar(select(League.tier).where(League.id == league_id))
    if tier is None:
        raise ValueError("Missing prize league")
    accounts = await locked_accounts(session, *(r.club_id for r in ranked))
    changed = False
    for place, row in enumerate(ranked, 1):
        changed |= await credit_locked(
            session,
            accounts[row.club_id],
            f"prize:{season_id}:{league_id}",
            "prize",
            season_prize(tier, place),
            at,
            {
                "season_id": str(season_id),
                "league_id": str(league_id),
                "place": place,
                "policy": POLICY_VERSION,
            },
        )
    return changed


@dataclass(frozen=True)
class FinanceScreen:
    club_name: str
    ownership_id: UUID
    balance: int
    reserved: int
    arrears: int
    weekly_player_wages: int
    weekly_staff_wages: int
    sponsor: int
    merchandise: int
    upkeep: int
    due_at: datetime | None
    last_cycle: FinanceCycle | None
    entries: tuple[LedgerEntry, ...]
    page: int
    pages: int


async def load_finances(
    session: AsyncSession, actor: Actor, page: int = 0, ownership_id: UUID | None = None
) -> FinanceScreen:
    ownership, club = await owned_club(session, actor)
    if ownership_id is not None and ownership_id != ownership.id:
        raise ValueError("Finance button belongs to an earlier owner")
    if type(page) is not int or page < 0:
        raise ValueError("Invalid finance page")
    account = (await locked_accounts(session, club.id))[club.id]
    contracts = (
        await session.scalars(
            select(SalaryContract).where(
                SalaryContract.club_id == club.id,
                SalaryContract.started_at <= actor.at,
                (SalaryContract.ended_at.is_(None)) | (SalaryContract.ended_at > actor.at),
            )
        )
    ).all()
    _, tier, place = await ranking_at(session, club.id, actor.at)
    count = (
        await session.scalar(
            select(func.count()).select_from(LedgerEntry).where(LedgerEntry.club_id == club.id)
        )
        or 0
    )
    pages = max(1, (count + 9) // 10)
    page = min(page, pages - 1)
    entries = (
        await session.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.club_id == club.id)
            .order_by(LedgerEntry.sequence.desc())
            .offset(page * 10)
            .limit(10)
        )
    ).all()
    due = await session.scalar(
        select(Job.due_at)
        .where(
            Job.kind == "finance.cycle",
            Job.payload["club_id"].astext == str(club.id),
            Job.status.in_(("held", "pending")),
        )
        .order_by(Job.due_at)
        .limit(1)
    )
    last = await session.scalar(
        select(FinanceCycle)
        .where(FinanceCycle.club_id == club.id)
        .order_by(FinanceCycle.week_start.desc())
        .limit(1)
    )
    await user_effect(
        session, actor, "finances_opened", Surface.CLUB, ownership=ownership, club_id=club.id
    )
    return FinanceScreen(
        club.name,
        ownership.id,
        club.budget,
        account.record.reserved,
        account.record.arrears,
        sum(c.weekly_rate for c in contracts if c.role == "player"),
        sum(c.weekly_rate for c in contracts if c.role != "player"),
        sponsor_income(tier, place),
        merchandise_income(club.supporters),
        stadium_upkeep(club.stadium_capacity),
        due,
        last,
        tuple(entries),
        page,
        pages,
    )
