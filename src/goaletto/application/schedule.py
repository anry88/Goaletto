"""Owned schedule queries; future placeholders never acquire an invented kickoff."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface
from goaletto.application.game_actions import Actor, PermissionDenied, owned_club, user_effect
from goaletto.domain.game_cycle import cycle_on
from goaletto.infrastructure.models import Club, Fixture, FixturePlan, Manager, Season, SeasonClub


@dataclass(frozen=True)
class ScheduleEntry:
    id: UUID
    day: date
    competition: str
    round_number: int
    home: str | None
    away: str | None
    venue: str
    kickoff: datetime | None
    conditional: bool
    stage: str | None
    leg: int | None
    status: str


@dataclass(frozen=True)
class ScheduleDay:
    day: date
    phase: str
    cycle: int
    entries: tuple[ScheduleEntry, ...]


@dataclass(frozen=True)
class ScheduleScreen:
    ownership_id: UUID
    club_name: str
    zone: str
    days: tuple[ScheduleDay, ...]
    page: int
    pages: int
    competition: str | None = None


async def load_schedule(
    session: AsyncSession,
    actor: Actor,
    page: int = 1,
    *,
    ownership_id: UUID | None = None,
    competition: str | None = None,
) -> ScheduleScreen:
    owner, club = await owned_club(session, actor)
    if ownership_id is not None and owner.id != ownership_id:
        raise PermissionDenied("Schedule belongs to an earlier owner")
    if type(page) is not int or page < 1 or competition not in (None, "cup", "champions"):
        raise ValueError("Invalid schedule page")
    season = await session.scalar(
        select(Season)
        .join(SeasonClub, SeasonClub.season_id == Season.id)
        .where(SeasonClub.club_id == club.id, Season.status.in_(("scheduled", "active")))
        .order_by(Season.number.desc())
        .limit(1)
    )
    if season is None or season.cycle_epoch is None:
        raise ValueError("Missing club calendar")
    manager = await session.get(Manager, actor.manager_id)
    until = actor.at + timedelta(days=10)
    query = select(FixturePlan).where(FixturePlan.season_id == season.id)
    if competition is None:
        query = query.where(
            FixturePlan.assigned_day >= actor.at.date(),
            FixturePlan.assigned_day <= until.date(),
            or_(
                FixturePlan.home_club_id == club.id,
                FixturePlan.away_club_id == club.id,
                FixturePlan.possible_club_ids.contains([str(club.id)]),
            ),
        )
    else:
        query = query.where(FixturePlan.competition == competition)
    plans = list(
        (
            await session.scalars(
                query.order_by(FixturePlan.assigned_day, FixturePlan.round_number, FixturePlan.id)
            )
        ).all()
    )
    ids = {p.id for p in plans}
    fixtures = {
        f.id: f for f in (await session.scalars(select(Fixture).where(Fixture.id.in_(ids)))).all()
    }
    names = {key: name for key, name in (await session.execute(select(Club.id, Club.name))).all()}
    entries: list[ScheduleEntry] = []
    for plan in plans:
        fixture = fixtures.get(plan.id)
        if fixture and fixture.status in ("cancelled", "abandoned"):
            continue
        if competition is None and fixture and not actor.at <= fixture.scheduled_start_at < until:
            if fixture.status not in ("live", "paused"):
                continue
        entries.append(
            ScheduleEntry(
                plan.id,
                plan.assigned_day,
                plan.competition,
                plan.round_number,
                names.get(plan.home_club_id),
                names.get(plan.away_club_id),
                "neutral"
                if plan.neutral
                else "home"
                if plan.home_club_id == club.id
                else "away"
                if plan.away_club_id == club.id
                else "pair"
                if plan.home_club_id and plan.away_club_id
                else "conditional",
                fixture.scheduled_start_at if fixture else None,
                plan.home_club_id is None or plan.away_club_id is None,
                plan.stage,
                plan.leg,
                fixture.status if fixture else "unannounced",
            )
        )
    if competition is None:
        all_days = tuple(actor.at.date() + timedelta(days=i) for i in range(11))
        pages = (len(all_days) + 4) // 5
        chosen = all_days[(page - 1) * 5 : page * 5]
    else:
        all_days = tuple(sorted({e.day for e in entries}))
        # Tournament rounds can have many pairs: paginate pairs, then group by day.
        pages = max(1, (len(entries) + 4) // 5)
        entries = entries[(page - 1) * 5 : page * 5]
        chosen = tuple(sorted({e.day for e in entries}))
    if page > pages:
        raise ValueError("Schedule page out of range")
    days = []
    for day in chosen:
        cycle = cycle_on(season.cycle_epoch, day)
        phase = cycle.phase.value if 0 <= cycle.index < 18 else "offseason"
        days.append(
            ScheduleDay(day, phase, cycle.index + 1, tuple(e for e in entries if e.day == day))
        )
    await user_effect(
        session, actor, "club_opened", Surface.CALENDAR, ownership=owner, club_id=club.id
    )
    return ScheduleScreen(
        owner.id,
        club.name,
        manager.timezone_name if manager else "UTC",
        tuple(days),
        page,
        pages,
        competition,
    )
