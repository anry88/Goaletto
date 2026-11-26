"""Owned schedule queries; future placeholders never acquire an invented kickoff."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface
from goaletto.application.game_actions import Actor, PermissionDenied, owned_club, user_effect
from goaletto.domain.game_cycle import cycle_on
from goaletto.infrastructure.models import (
    ChampionsTie,
    Club,
    CupTie,
    Fixture,
    FixturePlan,
    Manager,
    MatchResult,
    Season,
    SeasonClub,
    TrainingPlan,
)


@dataclass(frozen=True)
class ScheduleResult:
    score: tuple[int, int]
    first_leg: tuple[int, int] | None = None
    aggregate: tuple[int, int] | None = None
    extra_time: bool = False
    penalties: tuple[int, int] | None = None
    winner: str | None = None


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
    focus: str | None = None
    intensity: str | None = None
    result: ScheduleResult | None = None


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
    tournaments: tuple[str, ...] = ()


def _tie_id(plan: FixturePlan) -> UUID | None:
    prefix = {"cup": "tie", "champions": "champions-tie"}.get(plan.competition)
    parts = (plan.conditional_ref or "").split(":")
    if prefix is None or len(parts) != 4:
        return None
    kind, tie_id, marker, leg = parts
    if (kind, marker, leg) != (prefix, "leg", str(plan.leg)):
        return None
    try:
        return UUID(tie_id)
    except ValueError:
        return None


def _schedule_result(
    plan: FixturePlan,
    receipt: MatchResult,
    tie: CupTie | ChampionsTie | None,
    names: dict[UUID, str],
) -> ScheduleResult:
    score = (receipt.home_goals, receipt.away_goals)
    if tie is None or tie.winner_club_id is None:
        return ScheduleResult(score)
    first_leg = aggregate = penalties = None
    if plan.leg == 2 and (first := tie.first_match_result) is not None:
        first_leg = (first["home_goals"], first["away_goals"])
        # The home club in the first leg is away in the second leg.
        aggregate = (score[0] + first_leg[1], score[1] + first_leg[0])
    if (shootout := tie.shootout_result) is not None:
        scored = {str(receipt.home_club_id): 0, str(receipt.away_club_id): 0}
        for kick in shootout["kicks"]:
            if kick["scored"]:
                scored[kick["club_id"]] += 1
        penalties = (scored[str(receipt.home_club_id)], scored[str(receipt.away_club_id)])
    return ScheduleResult(
        score=score,
        first_leg=first_leg,
        aggregate=aggregate,
        extra_time=tie.extra_time_result is not None,
        penalties=penalties,
        winner=names[tie.winner_club_id],
    )


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
    allowed = (None, "cup", "champions", "friendly")
    if type(page) is not int or page < 1 or competition not in allowed:
        raise ValueError("Invalid schedule page")
    season = await session.scalar(
        select(Season)
        .join(SeasonClub, SeasonClub.season_id == Season.id)
        .where(SeasonClub.club_id == club.id, Season.status.in_(("scheduled", "active")))
        .order_by(Season.starts_at.desc(), Season.id)
        .limit(1)
    )
    if season is None or season.cycle_epoch is None:
        raise ValueError("Missing club calendar")
    manager = await session.get(Manager, actor.manager_id)
    until = actor.at + timedelta(days=10)
    today = actor.at.replace(hour=0, minute=0, second=0, microsecond=0)
    from goaletto.application.champions import host_seasons_for

    hosts = host_seasons_for(season.id)
    query = select(FixturePlan).where(
        or_(
            FixturePlan.season_id == season.id,
            (FixturePlan.competition == "champions") & FixturePlan.season_id.in_(hosts),
            (FixturePlan.competition == "friendly")
            & or_(FixturePlan.home_club_id == club.id, FixturePlan.away_club_id == club.id),
        )
    )
    tournaments: tuple[str, ...] = ()
    if competition is None:
        available = await session.scalars(
            query.with_only_columns(FixturePlan.competition)
            .where(FixturePlan.competition.in_(("cup", "champions")))
            .distinct()
        )
        tournaments = tuple(available)
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
    visible: list[FixturePlan] = []
    for plan in plans:
        fixture = fixtures.get(plan.id)
        if fixture and fixture.status in ("cancelled", "abandoned"):
            continue
        if competition is None and fixture and not today <= fixture.scheduled_start_at < until:
            if fixture.status not in ("live", "paused"):
                continue
        visible.append(plan)
    tie_ids = {plan.id: _tie_id(plan) for plan in visible}
    if competition is None:
        all_days = tuple(actor.at.date() + timedelta(days=i) for i in range(11))
        pages = (len(all_days) + 4) // 5
        chosen = all_days[(page - 1) * 5 : page * 5]
        visible = [plan for plan in visible if plan.assigned_day in chosen]
    else:
        # Paginate whole ties so their two legs stay on the same page.
        pairs: dict[UUID, list[FixturePlan]] = {}
        for plan in visible:
            pairs.setdefault(tie_ids[plan.id] or plan.id, []).append(plan)
        pages = max(1, (len(pairs) + 4) // 5)
        selected = {
            plan.id for pair in list(pairs.values())[(page - 1) * 5 : page * 5] for plan in pair
        }
        visible = [plan for plan in visible if plan.id in selected]
        chosen = tuple(sorted({plan.assigned_day for plan in visible}))
    if page > pages:
        raise ValueError("Schedule page out of range")

    finished_ids = [
        plan.id
        for plan in visible
        if (fixture := fixtures.get(plan.id)) is not None and fixture.status == "finished"
    ]
    receipts = (
        {
            receipt.fixture_id: receipt
            for receipt in (
                await session.scalars(
                    select(MatchResult).where(MatchResult.fixture_id.in_(finished_ids))
                )
            ).all()
        }
        if finished_ids
        else {}
    )
    ties: dict[UUID, CupTie | ChampionsTie] = {}
    models: tuple[tuple[str, type[CupTie | ChampionsTie]], ...] = (
        ("cup", CupTie),
        ("champions", ChampionsTie),
    )
    for tournament, model in models:
        decided_ids = {
            tie_ids[plan.id]
            for plan in visible
            if plan.id in receipts
            and plan.competition == tournament
            and (plan.leg == 2 or plan.neutral)
            and tie_ids[plan.id] is not None
        }
        if decided_ids:
            query_ties = select(model).where(model.id.in_(decided_ids), model.status == "decided")
            rows = await session.scalars(cast(Select[tuple[CupTie | ChampionsTie]], query_ties))
            ties.update((tie.id, tie) for tie in rows)
    names = {key: name for key, name in (await session.execute(select(Club.id, Club.name))).all()}
    entries: list[ScheduleEntry] = []
    for plan in visible:
        fixture = fixtures.get(plan.id)
        home_id = fixture.home_club_id if fixture else plan.home_club_id
        away_id = fixture.away_club_id if fixture else plan.away_club_id
        receipt = receipts.get(plan.id)
        tie_id = tie_ids[plan.id]
        # A first leg never announces advancement, even when browsing a settled tie.
        tie = ties.get(tie_id) if tie_id and (plan.leg == 2 or plan.neutral) else None
        entries.append(
            ScheduleEntry(
                plan.id,
                plan.assigned_day,
                plan.competition,
                plan.round_number,
                names.get(home_id),
                names.get(away_id),
                "neutral"
                if plan.neutral
                else "home"
                if home_id == club.id
                else "away"
                if away_id == club.id
                else "pair"
                if home_id and away_id
                else "conditional",
                (fixture.actual_start_at or fixture.scheduled_start_at) if fixture else None,
                home_id is None or away_id is None,
                plan.stage,
                plan.leg,
                fixture.status if fixture else "unannounced",
                result=_schedule_result(plan, receipt, tie, names) if receipt else None,
            )
        )
    if competition is None:
        trainings = (
            await session.scalars(
                select(TrainingPlan)
                .where(
                    TrainingPlan.club_id == club.id,
                    TrainingPlan.ownership_id == owner.id,
                    TrainingPlan.status.in_(("scheduled", "applied")),
                    TrainingPlan.week_start >= today,
                    TrainingPlan.week_start <= until,
                    TrainingPlan.due_at < until,
                )
                .order_by(TrainingPlan.week_start)
            )
        ).all()
        entries.extend(
            ScheduleEntry(
                t.id,
                t.week_start.date(),
                "training",
                0,
                None,
                None,
                "home",
                t.due_at,
                False,
                None,
                None,
                t.status,
                t.focus,
                t.intensity,
            )
            for t in trainings
        )
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
        tournaments,
    )
