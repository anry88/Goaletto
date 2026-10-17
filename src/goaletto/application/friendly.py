"""Voluntary friendlies: direct bot scheduling plus human challenges.

A manager challenges another club on a free-cycle UTC date. Bot clubs play
without an invitation; owned clubs require an explicit invitation that the
challenged owner accepts or declines. No automatic matchmaking, invitations
or match jobs are ever created: every row follows an explicit manager action.
"""

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID, uuid4, uuid5

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import Surface, require_utc
from goaletto.application.game_actions import (
    Actor,
    PermissionDenied,
    allocation_lock,
    owned_club,
    user_effect,
)
from goaletto.domain.game_cycle import HORIZON_DAYS, Phase, cycle_on, midnight
from goaletto.infrastructure.models import (
    Club,
    Fixture,
    FixturePlan,
    FriendlyInvitation,
    Manager,
    OutboxMessage,
    OwnershipPeriod,
    Season,
    SeasonClub,
    TrainingPlan,
)


async def _active_season(session: AsyncSession, club_id: UUID) -> Season:
    season = await session.scalar(
        select(Season)
        .join(SeasonClub, SeasonClub.season_id == Season.id)
        .where(
            SeasonClub.club_id == club_id,
            Season.status.in_(("scheduled", "active")),
        )
        .order_by(Season.starts_at.desc(), Season.id)
        .limit(1)
    )
    if season is None or season.cycle_epoch is None:
        raise ValueError("Missing club calendar")
    return season


def _require_future_free_day(today: date, day: date, epoch: date) -> None:
    if type(day) is not date:
        raise ValueError("Friendly day must be a UTC date")
    if not today < day <= today.fromordinal(today.toordinal() + HORIZON_DAYS):
        raise ValueError("Friendly day is outside the planning horizon")
    cycle = cycle_on(epoch, day)
    if not 0 <= cycle.index < 18 or cycle.phase != Phase.FREE:
        raise ValueError("Friendly matches are played on the free day of the cycle")


async def _clash_plan(
    session: AsyncSession,
    clubs: tuple[UUID, UUID],
    day: date,
    *,
    exclude: UUID | None = None,
) -> UUID | None:
    home_id, away_id = clubs
    filters = [
        FixturePlan.assigned_day == day,
        or_(
            FixturePlan.home_club_id.in_(clubs),
            FixturePlan.away_club_id.in_(clubs),
            FixturePlan.possible_club_ids.contains([str(home_id)]),
            FixturePlan.possible_club_ids.contains([str(away_id)]),
        ),
    ]
    if exclude is not None:
        filters.append(FixturePlan.id != exclude)
    found: UUID | None = await session.scalar(select(FixturePlan.id).where(*filters).limit(1))
    return found


def _plan_id(season_id: UUID, home_id: UUID, away_id: UUID, day: date) -> UUID:
    return uuid5(season_id, f"friendly:{home_id}:{away_id}:{day.isoformat()}")


async def _create_plan(
    session: AsyncSession,
    season: Season,
    home_id: UUID,
    away_id: UUID,
    day: date,
    at: datetime,
) -> FixturePlan:
    from goaletto.application.training import reserve_match_day

    clubs = (home_id, away_id)
    plan_id = _plan_id(season.id, home_id, away_id, day)
    existing = await session.get(FixturePlan, plan_id)
    if existing is not None:
        if (
            (existing.home_club_id, existing.away_club_id) != (home_id, away_id)
            or existing.season_id != season.id
            or existing.assigned_day != day
            or existing.competition != "friendly"
        ):
            raise ValueError("Conflicting friendly plan")
        return existing
    if await _clash_plan(session, clubs, day, exclude=plan_id) is not None:
        raise ValueError("A club already has a match that day")
    await reserve_match_day(session, clubs, day, at, competition="friendly")
    epoch = season.cycle_epoch
    assert epoch is not None
    cycle = cycle_on(epoch, day)
    plan = FixturePlan(
        id=plan_id,
        season_id=season.id,
        league_id=None,
        competition="friendly",
        round_number=cycle.index + 1,
        home_club_id=home_id,
        away_club_id=away_id,
        conditional_ref=None,
        possible_club_ids=[],
        stage=None,
        leg=None,
        neutral=False,
        assigned_day=day,
        revision=1,
    )
    session.add(plan)
    await session.flush()
    from goaletto.application.calendar_planning import plan_horizon

    await plan_horizon(session, season.id, at)
    if plan.blocked_reason is not None:
        raise ValueError("Friendly kickoff is blocked: " + plan.blocked_reason)
    return plan


async def schedule_friendly(
    session: AsyncSession, actor: Actor, away_club_id: UUID, day: date
) -> FixturePlan:
    """Schedule one friendly on a free-cycle UTC date against a bot club.

    Rules:
    - Only the current owner of the home club may schedule, via an explicit
      request. No automatic matchmaking, invitations or jobs are created.
    - ``day`` must be a strictly future UTC date within the 10-day horizon
      and fall on the FREE phase of the club's active four-day season.
    - Opponent must belong to the same season and currently be a bot club.
      Owned clubs play only by invitation: see :func:`invite_friendly`.
    - Either club having any FixturePlan that day (including conditional
      tournament slots) rejects the request. ``reserve_match_day`` with
      ``competition="friendly"`` additionally rejects scheduled/applied
      training that day; official matches would cancel training instead.
    - The plan is structural only; kickoff/jobs materialize via the shared
      10-day horizon and stay fixed once announced. The challenger hosts and
      receives the ticket income.
    """

    require_utc(actor.at)
    if not session.in_transaction():
        raise ValueError("Friendly scheduling needs a transaction")
    ownership, home = await owned_club(session, actor)
    away = await session.get(Club, away_club_id)
    if away is None or away.id == home.id:
        raise ValueError("Friendly needs a different opponent club")
    season = await _active_season(session, home.id)
    epoch = season.cycle_epoch
    assert epoch is not None
    _require_future_free_day(actor.at.date(), day, epoch)
    opponent_membership = await session.scalar(
        select(SeasonClub).where(SeasonClub.season_id == season.id, SeasonClub.club_id == away.id)
    )
    if opponent_membership is None:
        raise ValueError("Friendly opponent must share the season")
    active_owner: UUID | None = await session.scalar(
        select(OwnershipPeriod.id).where(
            OwnershipPeriod.club_id == away.id,
            OwnershipPeriod.ended_at.is_(None),
            OwnershipPeriod.expires_at > actor.at,
        )
    )
    if active_owner is not None:
        raise ValueError("Owned clubs play friendlies only by invitation")
    plan = await _create_plan(session, season, home.id, away.id, day, actor.at)
    existing = await session.scalar(
        select(FriendlyInvitation).where(FriendlyInvitation.plan_id == plan.id)
    )
    if existing is None:
        session.add(
            FriendlyInvitation(
                id=uuid5(plan.id, "friendly-booking"),
                season_id=season.id,
                home_club_id=home.id,
                away_club_id=away.id,
                challenger_ownership_id=ownership.id,
                challenged_ownership_id=None,
                assigned_day=day,
                status="accepted",
                created_at=actor.at,
                responded_at=actor.at,
                plan_id=plan.id,
            )
        )
    await user_effect(
        session, actor, "friendly_scheduled", Surface.CALENDAR, ownership=ownership, club_id=home.id
    )
    return plan


async def _pending_invite(
    session: AsyncSession,
    season_id: UUID,
    first_id: UUID,
    second_id: UUID,
    day: date,
    *,
    exclude: UUID | None = None,
) -> FriendlyInvitation | None:
    filters = [
        FriendlyInvitation.season_id == season_id,
        FriendlyInvitation.assigned_day == day,
        FriendlyInvitation.status == "pending",
        or_(
            (FriendlyInvitation.home_club_id == first_id)
            & (FriendlyInvitation.away_club_id == second_id),
            (FriendlyInvitation.home_club_id == second_id)
            & (FriendlyInvitation.away_club_id == first_id),
        ),
    ]
    if exclude is not None:
        filters.append(FriendlyInvitation.id != exclude)
    row: FriendlyInvitation | None = await session.scalar(
        select(FriendlyInvitation).where(*filters).order_by(FriendlyInvitation.created_at).limit(1)
    )
    return row


def _settle_expiry(row: FriendlyInvitation, at: datetime) -> bool:
    """Lazily expire a pending invitation past its UTC date. Returns True if open."""
    if row.status == "pending" and at.date() >= row.assigned_day:
        row.status, row.responded_at = "expired", at
        return False
    return row.status == "pending"


async def invite_friendly(
    session: AsyncSession, actor: Actor, away_club_id: UUID, day: date
) -> FriendlyInvitation:
    """Challenge an owned club to a friendly on a free UTC date.

    The invitation stays pending until the challenged owner accepts or
    declines, or the challenger cancels. Accepting creates the FixturePlan;
    until then no plan, kickoff or jobs exist and neither club's training is
    touched. The invitation expires at the start of its assigned UTC date, before any
    kickoff can occur.
    """

    require_utc(actor.at)
    if not session.in_transaction():
        raise ValueError("Friendly challenges need a transaction")
    ownership, home = await owned_club(session, actor)
    away = await session.get(Club, away_club_id)
    if away is None or away.id == home.id:
        raise ValueError("Friendly needs a different opponent club")
    season = await _active_season(session, home.id)
    epoch = season.cycle_epoch
    assert epoch is not None
    _require_future_free_day(actor.at.date(), day, epoch)
    opponent_membership = await session.scalar(
        select(SeasonClub).where(SeasonClub.season_id == season.id, SeasonClub.club_id == away.id)
    )
    if opponent_membership is None:
        raise ValueError("Friendly opponent must share the season")
    bot_check: UUID | None = await session.scalar(
        select(OwnershipPeriod.id).where(
            OwnershipPeriod.club_id == away.id,
            OwnershipPeriod.ended_at.is_(None),
            OwnershipPeriod.expires_at > actor.at,
        )
    )
    if bot_check is None:
        raise ValueError("Bot clubs need no invitation; schedule the friendly directly")
    if await _clash_plan(session, (home.id, away.id), day) is not None:
        raise ValueError("A club already has a match that day")
    duplicate = await _pending_invite(session, season.id, home.id, away.id, day)
    if duplicate is not None:
        same_owners = (
            duplicate.challenger_ownership_id == ownership.id
            and duplicate.challenged_ownership_id == bot_check
            if duplicate.home_club_id == home.id
            else duplicate.challenger_ownership_id == bot_check
            and duplicate.challenged_ownership_id == ownership.id
        )
        if same_owners and _settle_expiry(duplicate, actor.at):
            return duplicate
        duplicate.status, duplicate.responded_at = "expired", actor.at
        await session.flush()
    from goaletto.application.training import reserve_match_day

    await reserve_match_day(session, (home.id, away.id), day, actor.at, competition="friendly")
    row = FriendlyInvitation(
        id=uuid4(),
        season_id=season.id,
        home_club_id=home.id,
        away_club_id=away.id,
        challenger_ownership_id=ownership.id,
        challenged_ownership_id=bot_check,
        assigned_day=day,
        status="pending",
        created_at=actor.at,
    )
    session.add(row)
    # Duplicate pending rows are already excluded above under the shared
    # allocation lock; the partial unique index is a durable backstop.
    await session.flush()
    target = await session.get(OwnershipPeriod, bot_check)
    assert target is not None
    manager = await session.get(Manager, target.manager_id)
    assert manager is not None
    session.add(
        OutboxMessage(
            id=uuid5(row.id, "invite-notice"),
            dedupe_key=f"friendly:invite:{row.id}",
            priority=70,
            chat_id=manager.telegram_id,
            due_at=actor.at,
            status="pending",
            attempts=0,
            payload={
                "version": 1,
                "kind": "friendly_invitation",
                "ownership_id": str(target.id),
                "club_id": str(away.id),
                "invitation_id": str(row.id),
                "home": home.name,
                "day": day.isoformat(),
            },
        )
    )
    await user_effect(
        session, actor, "friendly_invited", Surface.CALENDAR, ownership=ownership, club_id=home.id
    )
    return row


async def respond_friendly(
    session: AsyncSession, actor: Actor, invitation_id: UUID, *, accept: bool
) -> FriendlyInvitation:
    """Accept or decline a challenge; the challenger cancels with ``accept=False``.

    - Accept requires the current owner of the challenged club. It rechecks
      the season, the free day, both clubs' match-day clashes and both clubs'
      training, then creates the FixturePlan atomically with the acceptance.
    - Decline requires the challenged owner; cancel requires the challenger.
    - Repeated responses return the settled row without a second plan.
    """

    require_utc(actor.at)
    if not session.in_transaction():
        raise ValueError("Friendly responses need a transaction")
    await allocation_lock(session)
    ownership, club = await owned_club(session, actor)
    row = await session.get(FriendlyInvitation, invitation_id, with_for_update=True)
    if row is None:
        raise ValueError("Unknown friendly invitation")
    if club.id not in (row.home_club_id, row.away_club_id):
        raise PermissionDenied("Invitation belongs to another pair of clubs")
    expected_owner = (
        row.challenger_ownership_id if club.id == row.home_club_id else row.challenged_ownership_id
    )
    if ownership.id != expected_owner:
        raise PermissionDenied("Invitation belongs to a previous owner")
    if row.status != "pending":
        return row
    if not _settle_expiry(row, actor.at):
        await session.flush()
        return row
    if accept:
        if club.id != row.away_club_id:
            raise PermissionDenied("Only the challenged club accepts the invitation")
        challenger = await session.get(OwnershipPeriod, row.challenger_ownership_id)
        if (
            challenger is None
            or challenger.ended_at is not None
            or challenger.expires_at <= actor.at
            or challenger.club_id != row.home_club_id
        ):
            row.status, row.responded_at = "expired", actor.at
            await session.flush()
            return row
        season = await session.get(Season, row.season_id)
        if (
            season is None
            or season.cycle_epoch is None
            or season.status not in {"scheduled", "active"}
        ):
            raise ValueError("Missing club calendar")
        for club_id in (row.home_club_id, row.away_club_id):
            membership = await session.scalar(
                select(SeasonClub).where(
                    SeasonClub.season_id == row.season_id, SeasonClub.club_id == club_id
                )
            )
            if membership is None:
                raise ValueError("Friendly clubs must share the season")
        cycle = cycle_on(season.cycle_epoch, row.assigned_day)
        if not 0 <= cycle.index < 18 or cycle.phase != Phase.FREE:
            raise ValueError("Friendly matches are played on the free day of the cycle")
        plan = await _create_plan(
            session, season, row.home_club_id, row.away_club_id, row.assigned_day, actor.at
        )
        row.status, row.responded_at, row.plan_id = "accepted", actor.at, plan.id
        await session.execute(
            update(FriendlyInvitation)
            .where(
                FriendlyInvitation.season_id == row.season_id,
                FriendlyInvitation.assigned_day == row.assigned_day,
                FriendlyInvitation.status == "pending",
                FriendlyInvitation.id != row.id,
                or_(
                    (FriendlyInvitation.home_club_id == row.home_club_id)
                    & (FriendlyInvitation.away_club_id == row.away_club_id),
                    (FriendlyInvitation.home_club_id == row.away_club_id)
                    & (FriendlyInvitation.away_club_id == row.home_club_id),
                ),
            )
            .values(status="cancelled", responded_at=actor.at)
        )
        await session.flush()
        await user_effect(
            session,
            actor,
            "friendly_responded",
            Surface.CALENDAR,
            ownership=ownership,
            club_id=club.id,
        )
        return row
    if club.id == row.away_club_id:
        row.status = "declined"
    elif club.id == row.home_club_id:
        if ownership.id != row.challenger_ownership_id:
            raise PermissionDenied("Challenge belongs to an earlier owner")
        row.status = "cancelled"
    else:
        raise PermissionDenied("Invitation belongs to another pair of clubs")
    row.responded_at = actor.at
    await user_effect(
        session, actor, "friendly_responded", Surface.CALENDAR, ownership=ownership, club_id=club.id
    )
    await session.flush()
    return row


@dataclass(frozen=True)
class FriendlyInviteCard:
    id: UUID
    home_club_id: UUID
    away_club_id: UUID
    home_name: str
    away_name: str
    day: date
    status: str


@dataclass(frozen=True)
class FriendlyScreen:
    ownership_id: UUID
    club_id: UUID
    club_name: str
    incoming: tuple[FriendlyInviteCard, ...]
    outgoing: tuple[FriendlyInviteCard, ...]
    recent: tuple[FriendlyInviteCard, ...]


def _card(row: FriendlyInvitation, names: dict[UUID, str]) -> FriendlyInviteCard:
    return FriendlyInviteCard(
        row.id,
        row.home_club_id,
        row.away_club_id,
        names.get(row.home_club_id, "?"),
        names.get(row.away_club_id, "?"),
        row.assigned_day,
        row.status,
    )


async def load_friendly_screen(
    session: AsyncSession, actor: Actor, *, record: bool = True
) -> FriendlyScreen:
    """Pending incoming/outgoing challenges plus the latest settled ones."""
    ownership, club = await owned_club(session, actor)
    rows = (
        await session.scalars(
            select(FriendlyInvitation)
            .where(
                or_(
                    FriendlyInvitation.challenger_ownership_id == ownership.id,
                    FriendlyInvitation.challenged_ownership_id == ownership.id,
                )
            )
            .order_by(
                (FriendlyInvitation.status == "pending").desc(),
                FriendlyInvitation.created_at.desc(),
            )
            .limit(20)
        )
    ).all()
    touched = False
    for row in rows:
        if row.status == "pending" and not _settle_expiry(row, actor.at):
            touched = True
    if touched:
        await session.flush()
    name_rows = await session.execute(
        select(Club.id, Club.name).where(
            Club.id.in_({r.home_club_id for r in rows} | {r.away_club_id for r in rows} | {club.id})
        )
    )
    names: dict[UUID, str] = {club_id: name for club_id, name in name_rows.all()}
    incoming = tuple(
        _card(r, names) for r in rows if r.status == "pending" and r.away_club_id == club.id
    )
    outgoing = tuple(
        _card(r, names) for r in rows if r.status == "pending" and r.home_club_id == club.id
    )
    recent = tuple(_card(r, names) for r in rows if r.status != "pending")[:5]
    if record:
        await user_effect(
            session,
            actor,
            "friendly_opened",
            Surface.CALENDAR,
            ownership=ownership,
            club_id=club.id,
        )
    return FriendlyScreen(
        ownership.id, club.id, names.get(club.id, "?"), incoming, outgoing, recent
    )


@dataclass(frozen=True)
class FriendlyOpponent:
    club_id: UUID
    name: str
    owned: bool
    busy: bool


async def challenge_days(session: AsyncSession, actor: Actor) -> tuple[date, ...]:
    """Free UTC dates in the horizon without my match plan or training."""
    ownership, club = await owned_club(session, actor)
    _ = ownership
    season = await _active_season(session, club.id)
    epoch = season.cycle_epoch
    assert epoch is not None
    today = actor.at.date()
    days = tuple(
        today.fromordinal(today.toordinal() + offset) for offset in range(1, HORIZON_DAYS + 1)
    )
    free = tuple(
        day
        for day in days
        if 0 <= cycle_on(epoch, day).index < 18 and cycle_on(epoch, day).phase == Phase.FREE
    )
    if not free:
        return ()
    plans = {
        plan.assigned_day
        for plan in (
            await session.scalars(
                select(FixturePlan).where(
                    FixturePlan.assigned_day.in_(free),
                    or_(
                        FixturePlan.home_club_id == club.id,
                        FixturePlan.away_club_id == club.id,
                        FixturePlan.possible_club_ids.contains([str(club.id)]),
                    ),
                )
            )
        ).all()
    }
    busy_training = {
        plan.week_start.date()
        for plan in (
            await session.scalars(
                select(TrainingPlan).where(
                    TrainingPlan.club_id == club.id,
                    TrainingPlan.week_start.in_([midnight(day) for day in free]),
                    TrainingPlan.status.in_(("scheduled", "applied")),
                )
            )
        ).all()
    }
    return tuple(day for day in free if day not in plans and day not in busy_training)


async def challenge_opponents(
    session: AsyncSession, actor: Actor, day: date
) -> tuple[FriendlyOpponent, ...]:
    """Every other club of the season with ownership and busyness markers."""
    _, club = await owned_club(session, actor)
    season = await _active_season(session, club.id)
    if type(day) is not date:
        raise ValueError("Friendly day must be a UTC date")
    members = (
        await session.scalars(
            select(SeasonClub).where(
                SeasonClub.season_id == season.id, SeasonClub.club_id != club.id
            )
        )
    ).all()
    if not members:
        return ()
    ids = [m.club_id for m in members]
    name_rows = await session.execute(select(Club.id, Club.name).where(Club.id.in_(ids)))
    names: dict[UUID, str] = {club_id: name for club_id, name in name_rows.all()}
    owned_ids = {
        row.club_id
        for row in (
            await session.scalars(
                select(OwnershipPeriod).where(
                    OwnershipPeriod.club_id.in_(ids),
                    OwnershipPeriod.ended_at.is_(None),
                    OwnershipPeriod.expires_at > actor.at,
                )
            )
        ).all()
    }
    plans = (
        await session.scalars(
            select(FixturePlan).where(
                FixturePlan.assigned_day == day,
                or_(
                    FixturePlan.home_club_id.in_(ids),
                    FixturePlan.away_club_id.in_(ids),
                    *(
                        FixturePlan.possible_club_ids.contains([str(cid)])
                        for cid in (*ids, club.id)
                    ),
                ),
            )
        )
    ).all()
    busy: set[UUID] = set()
    for plan in plans:
        for cid in (plan.home_club_id, plan.away_club_id):
            if cid is not None and cid in ids:
                busy.add(cid)
        for raw in plan.possible_club_ids:
            try:
                parsed = UUID(raw)
            except ValueError:
                continue
            if parsed in ids:
                busy.add(parsed)
    busy.update(
        (
            await session.scalars(
                select(TrainingPlan.club_id).where(
                    TrainingPlan.club_id.in_(ids),
                    TrainingPlan.week_start == midnight(day),
                    TrainingPlan.status.in_(("scheduled", "applied")),
                )
            )
        ).all()
    )
    return tuple(
        FriendlyOpponent(cid, names.get(cid, "?"), cid in owned_ids, cid in busy)
        for cid in sorted(ids, key=str)
    )


async def authenticate_fixture(session: AsyncSession, fixture: "Fixture") -> None:
    from goaletto.infrastructure.models import FixturePlan

    plan = await session.get(FixturePlan, fixture.id)
    booking = await session.scalar(
        select(FriendlyInvitation).where(
            FriendlyInvitation.plan_id == fixture.id,
            FriendlyInvitation.status == "accepted",
        )
    )
    if (
        plan is None
        or booking is None
        or plan.competition != "friendly"
        or fixture.competition != "friendly"
        or fixture.neutral
        or fixture.leg is not None
        or plan.id
        != _plan_id(
            plan.season_id, booking.home_club_id, booking.away_club_id, booking.assigned_day
        )
        or (plan.home_club_id, plan.away_club_id) != (booking.home_club_id, booking.away_club_id)
        or (fixture.home_club_id, fixture.away_club_id)
        != (booking.home_club_id, booking.away_club_id)
        or plan.season_id != fixture.season_id
        or plan.announced_at is None
        or plan.assigned_day != booking.assigned_day
        or plan.assigned_day != fixture.scheduled_start_at.date()
    ):
        raise ValueError("Friendly fixture has no genuine accepted request")
