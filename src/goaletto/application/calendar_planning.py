"""Durable rolling announcement horizon. No Telegram or wall-clock reads."""

from datetime import datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.application.game_actions import allocation_lock
from goaletto.application.kickoff_preferences import fixture_kickoff_decision
from goaletto.domain.calendar import ScheduledFixture, ScheduledWork, fixture_work
from goaletto.domain.game_cycle import (
    CYCLE_CALENDAR,
    HORIZON_DAYS,
    Phase,
    cycle_on,
    midnight,
    season_end,
    season_epoch,
)
from goaletto.domain.world import stable_seed
from goaletto.infrastructure.models import Fixture, FixturePlan, Job, Season

# Covers regulation and future extra time/penalties. Actual unfinished games are
# also checked at runtime; the reserve is not a promise of 24 hours of recovery.
OCCUPANCY = timedelta(hours=2)


def season_work(season_id: UUID, first: datetime) -> tuple[ScheduledWork, ...]:
    return (
        ScheduledWork(
            f"calendar:v2:{season_id}:open", "season.open", midnight(season_epoch(first))
        ),
        ScheduledWork(f"calendar:v2:{season_id}:rollover", "season.rollover", season_end(first)),
    )


def job_row(
    season_id: UUID, work: ScheduledWork, status: str = "pending", *, revision: int = 1
) -> dict[str, object]:
    key = f"{work.dedupe_key}:revision:{revision}"
    return dict(
        id=uuid5(season_id, key),
        dedupe_key=key,
        kind=work.kind,
        due_at=work.due_at,
        status=status,
        attempts=0,
        payload=dict(
            version=1,
            calendar_version=CYCLE_CALENDAR,
            season_id=str(season_id),
            fixture_id=str(work.fixture_id) if work.fixture_id else None,
            tick_number=work.tick_number,
        ),
    )


async def add_plan(session: AsyncSession, fixture: ScheduledFixture, at: datetime) -> None:
    from goaletto.application.training import reserve_match_day

    await reserve_match_day(
        session,
        (fixture.home_club_id, fixture.away_club_id),
        fixture.kickoff.date(),
        at,
        competition="league",
    )
    await session.execute(
        insert(FixturePlan)
        .values(
            id=fixture.id,
            season_id=fixture.season_id,
            league_id=fixture.league_id,
            round_number=fixture.round_number,
            home_club_id=fixture.home_club_id,
            away_club_id=fixture.away_club_id,
            assigned_day=fixture.kickoff.date(),
            competition="league",
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )


async def plan_horizon(session: AsyncSession, season_id: UUID, at: datetime) -> int:
    require_utc(at)
    await allocation_lock(session)
    season = await session.get(Season, season_id)
    if season is None or season.calendar_policy != CYCLE_CALENDAR:
        raise ValueError("Unknown four-day season")
    if not season.calendar_enabled or season.status not in {"scheduled", "active"}:
        return 0
    until = at + timedelta(days=HORIZON_DAYS)
    plans = list(
        (
            await session.scalars(
                select(FixturePlan)
                .where(
                    FixturePlan.season_id == season_id,
                    FixturePlan.announced_at.is_(None),
                    FixturePlan.assigned_day >= at.date(),
                    FixturePlan.assigned_day <= until.date(),
                )
                .order_by(FixturePlan.assigned_day, FixturePlan.id)
                .with_for_update()
            )
        ).all()
    )
    made = 0
    for plan in plans:
        required_phase = {
            "league": Phase.LEAGUE,
            "cup": Phase.CUP,
            "champions": Phase.CHAMPIONS,
            "friendly": Phase.FREE,
        }[plan.competition]
        if (
            season.cycle_epoch is not None
            and cycle_on(season.cycle_epoch, plan.assigned_day).phase != required_phase
        ):
            plan.blocked_reason = "invalid_competition_day"
            continue
        # Conditional tournament slots wait for actual persisted winners.
        if plan.home_club_id is None or plan.away_club_id is None:
            plan.blocked_reason = "awaiting_participants"
            continue
        if plan.competition not in ("league", "cup") or (
            plan.competition == "league" and plan.league_id is None
        ):
            plan.blocked_reason = "awaiting_competition_runtime"
            continue
        seed = stable_seed(season.seed, f"kickoff:{plan.id}:{plan.revision}")
        # Pure preview before pinning a durable decision: an exact upper boundary
        # must remain unfixed so later preferences can still apply.
        from goaletto.application.kickoff_preferences import preview_fixture_kickoff

        decision = await preview_fixture_kickoff(
            session,
            day=plan.assigned_day,
            home_club_id=plan.home_club_id,
            away_club_id=plan.away_club_id,
            at=at,
            seed=seed,
        )
        if decision.kickoff >= until:
            continue
        if decision.kickoff - timedelta(minutes=5, seconds=1) <= at:
            # Outages never fire missed lock/start/ticks in a burst. Operator may
            # explicitly recover/replan this unannounced date after inspection.
            plan.blocked_reason = "missed_preparation_window"
            continue
        conflicts = await session.scalar(
            select(Fixture.id)
            .where(
                Fixture.status.in_(("scheduled", "live", "paused")),
                or_(
                    Fixture.home_club_id.in_((plan.home_club_id, plan.away_club_id)),
                    Fixture.away_club_id.in_((plan.home_club_id, plan.away_club_id)),
                ),
                Fixture.scheduled_start_at > decision.kickoff - OCCUPANCY,
                Fixture.scheduled_start_at < decision.kickoff + OCCUPANCY,
            )
            .limit(1)
        )
        if conflicts is not None:
            plan.blocked_reason = "fixture_overlap"
            continue
        from goaletto.application.training import reserve_match_day

        await reserve_match_day(
            session,
            (plan.home_club_id, plan.away_club_id),
            plan.assigned_day,
            at,
            competition=plan.competition,
        )
        decision = await fixture_kickoff_decision(
            session,
            fixture_key=plan.id,
            day=plan.assigned_day,
            home_club_id=plan.home_club_id,
            away_club_id=plan.away_club_id,
            at=at,
            seed=seed,
        )
        fixture = ScheduledFixture(
            plan.id,
            season.id,
            plan.league_id,
            plan.round_number,
            plan.home_club_id,
            plan.away_club_id,
            decision.kickoff,
            competition=plan.competition,
            stage=plan.stage,
            leg=plan.leg,
            neutral=plan.neutral,
        )
        session.add(
            Fixture(
                id=fixture.id,
                season_id=fixture.season_id,
                league_id=fixture.league_id,
                competition=fixture.competition,
                stage=fixture.stage,
                leg=fixture.leg,
                neutral=fixture.neutral,
                round_number=fixture.round_number,
                home_club_id=fixture.home_club_id,
                away_club_id=fixture.away_club_id,
                scheduled_start_at=fixture.kickoff,
                lineup_lock_at=fixture.lineup_lock_at,
                status="scheduled",
            )
        )
        await session.flush()
        for work in fixture_work(fixture):
            await session.execute(
                insert(Job)
                .values(**job_row(season.id, work, revision=plan.revision))
                .on_conflict_do_nothing(index_elements=["dedupe_key"])
            )
        plan.announced_at, plan.planning_until, plan.blocked_reason = at, until, None
        made += 1
    await session.flush()
    return made


async def ensure_planner_job(
    session: AsyncSession, season_id: UUID, at: datetime, *, held: bool = False
) -> None:
    key = f"calendar:v2:{season_id}:horizon"
    await session.execute(
        insert(Job)
        .values(
            id=uuid5(season_id, key),
            dedupe_key=key,
            kind="calendar.materialize",
            payload={"version": 1, "season_id": str(season_id)},
            due_at=at,
            status="held" if held else "pending",
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )


async def recover_unannounced_plan(
    session: AsyncSession, plan_id: UUID, day: datetime, at: datetime
) -> int:
    """Explicit operator recovery. Announced fixtures are never moved here."""
    require_utc(day)
    require_utc(at)
    await allocation_lock(session)
    plan = await session.get(FixturePlan, plan_id, with_for_update=True)
    if plan is None or plan.announced_at is not None or day.date() <= at.date():
        raise ValueError("Recovery requires an unannounced plan and a future UTC date")
    season = await session.get(Season, plan.season_id)

    required = {
        "league": Phase.LEAGUE,
        "cup": Phase.CUP,
        "champions": Phase.CHAMPIONS,
        "friendly": Phase.FREE,
    }[plan.competition]
    if (
        season is None
        or season.cycle_epoch is None
        or cycle_on(season.cycle_epoch, day.date()).phase != required
    ):
        raise ValueError("Recovery must retain the competition phase")
    from goaletto.application.training import reserve_match_day

    participants = {c for c in (plan.home_club_id, plan.away_club_id) if c is not None}
    participants.update(UUID(c) for c in plan.possible_club_ids)
    await reserve_match_day(
        session, tuple(sorted(participants)), day.date(), at, competition=plan.competition
    )
    plan.assigned_day, plan.revision, plan.blocked_reason = day.date(), plan.revision + 1, None
    await session.flush()
    return await plan_horizon(session, plan.season_id, at)
