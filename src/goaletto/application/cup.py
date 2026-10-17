"""Persisted cup draw, real-engine results, progression and neutral venue receipts."""

from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.application.game_actions import allocation_lock
from goaletto.application.money import credit_locked, locked_accounts
from goaletto.application.training import reserve_match_day
from goaletto.domain.cup_bracket import (
    CupBracket,
    CupEntry,
    all_matches,
    build_bracket,
    possible_clubs,
    validate_winners,
)
from goaletto.domain.knockout import KNOCKOUT_POLICY, KnockoutTie, MatchResult
from goaletto.domain.knockout_match import KnockoutContext, decision
from goaletto.domain.match_codec import restore_state, state_hash
from goaletto.domain.match_types import KNOCKOUT_ENGINE_VERSION
from goaletto.domain.neutral_final import (
    NEUTRAL_DEMAND_BPS,
    NEUTRAL_FINAL_POLICY,
    NeutralFinalSnapshot,
    settle_neutral_final,
)
from goaletto.domain.stadium import Attendance
from goaletto.infrastructure.models import (
    Club,
    Fixture,
    FixturePlan,
    League,
    Match,
    NeutralFinalBooking,
    Season,
    SeasonClub,
)
from goaletto.infrastructure.models import CupBracket as CupBracketModel
from goaletto.infrastructure.models import CupTie as CupTieModel
from goaletto.infrastructure.models import MatchResult as ResultReceipt

CUP_FINAL_TICKET_PRICE = 5


def _dump(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _dump(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_dump(v) for v in value]
    return value


def _store_segment(result: MatchResult) -> dict[str, Any]:
    payload = _dump(asdict(result))
    assert isinstance(payload, dict)
    return payload


def _load_segment(payload: dict[str, Any]) -> MatchResult:
    data = dict(payload)
    data["home_id"], data["away_id"] = UUID(data["home_id"]), UUID(data["away_id"])
    return MatchResult(**data)


async def frozen_bracket(session: AsyncSession, row: CupBracketModel) -> CupBracket:
    entries = tuple(
        CupEntry(UUID(e["league_id"]), e["tier"], tuple(UUID(c) for c in e["order"]))
        for e in row.entry_snapshot
    )
    graph = build_bracket(
        season_id=row.season_id, league_set_id=row.league_set_id, entries=entries, seed=row.seed
    )
    if graph.policy_version != row.policy_version:
        raise ValueError("Unknown persisted cup draw policy")
    return graph


async def initialize_season_cup(
    session: AsyncSession,
    season_id: UUID,
    at: datetime,
) -> CupBracketModel:
    """Durable bracket construction, 49 ties and 97 fixture plans."""
    require_utc(at)
    await allocation_lock(session)

    existing = await session.get(CupBracketModel, season_id)
    if existing is not None:
        await frozen_bracket(session, existing)
        return existing

    season = await session.get(Season, season_id)
    if season is None:
        raise ValueError("Season not found")
    if season.cycle_epoch is None:
        raise ValueError("Season cycle epoch required for cup planning")

    # Fetch 50 clubs across tiers 1..5 for this season
    memberships = (
        await session.execute(
            select(SeasonClub, League.tier)
            .join(League, League.id == SeasonClub.league_id)
            .where(SeasonClub.season_id == season_id)
            .order_by(League.tier, SeasonClub.tie_break_order, SeasonClub.club_id)
        )
    ).all()

    by_tier: dict[int, list[UUID]] = {tier: [] for tier in range(1, 6)}
    league_by_tier: dict[int, set[UUID]] = {tier: set() for tier in range(1, 6)}
    for sc, tier in memberships:
        by_tier[tier].append(sc.club_id)
        league_by_tier[tier].add(sc.league_id)

    for tier in range(1, 6):
        if len(by_tier[tier]) != 10:
            raise ValueError(f"Tier {tier} must have exactly ten clubs, got {len(by_tier[tier])}")
        if len(league_by_tier[tier]) != 1:
            raise ValueError(f"Tier {tier} must belong to exactly one league")

    entries = tuple(
        CupEntry(
            league_id=next(iter(league_by_tier[tier])),
            tier=tier,
            order=tuple(by_tier[tier]),
        )
        for tier in range(1, 6)
    )

    domain_bracket = build_bracket(
        season_id=season_id,
        league_set_id=season.league_set_id,
        entries=entries,
        seed=season.seed,
    )

    bracket_row = CupBracketModel(
        season_id=season_id,
        league_set_id=season.league_set_id,
        seed=season.seed,
        policy_version=domain_bracket.policy_version,
        created_at=at,
        entry_snapshot=_dump([asdict(e) for e in entries]),
    )
    session.add(bracket_row)

    for tie in domain_bracket.ties:
        tie_row = CupTieModel(
            id=tie.id,
            season_id=season_id,
            round_key=tie.round_key,
            round_label=tie.round_label,
            home_kind=tie.home.kind,
            home_club_id=tie.home.club_id,
            home_tie_id=tie.home.tie_id,
            away_kind=tie.away.kind,
            away_club_id=tie.away.club_id,
            away_tie_id=tie.away.tie_id,
            cup_days=list(tie.cup_days),
            neutral=tie.neutral,
            status="pending",
        )
        session.add(tie_row)

    await session.flush()

    # Create 97 fixture plans for the 4-day cycles (day 1 of cycle is cup day)
    for leg in all_matches(domain_bracket):
        plan_id = uuid5(season_id, f"cup:{leg.tie_id}:{leg.leg_number}")
        assigned_day = season.cycle_epoch + timedelta(days=4 * (leg.cup_day - 1))

        candidates: set[UUID] = set()
        if leg.home.kind == "club" and leg.home.club_id is not None:
            candidates.add(leg.home.club_id)
        else:
            candidates.update(possible_clubs(domain_bracket, leg.home, {}))

        if leg.away.kind == "club" and leg.away.club_id is not None:
            candidates.add(leg.away.club_id)
        else:
            candidates.update(possible_clubs(domain_bracket, leg.away, {}))

        plan = FixturePlan(
            id=plan_id,
            season_id=season_id,
            league_id=None,
            competition="cup",
            round_number=leg.cup_day,
            stage=leg.round_key,
            leg=leg.leg_number,
            neutral=leg.neutral,
            assigned_day=assigned_day,
            home_club_id=leg.home.club_id if leg.home.kind == "club" else None,
            away_club_id=leg.away.club_id if leg.away.kind == "club" else None,
            conditional_ref=f"tie:{leg.tie_id}:leg:{leg.leg_number}",
            possible_club_ids=[str(c) for c in sorted(candidates, key=str)],
        )
        session.add(plan)
        await reserve_match_day(session, tuple(candidates), assigned_day, at, competition="cup")

    await session.flush()
    return bracket_row


async def context_for_fixture(session: AsyncSession, fixture: Fixture) -> KnockoutContext:
    """Authenticate the exact frozen tie/leg; no relabelled operator admission."""
    plan = await session.get(FixturePlan, fixture.id)
    if (
        fixture.competition != "cup"
        or plan is None
        or plan.competition != "cup"
        or plan.conditional_ref is None
    ):
        raise ValueError("Cup fixture needs a genuine cup plan")
    parts = plan.conditional_ref.split(":")
    if len(parts) != 4 or parts[0] != "tie" or parts[2] != "leg":
        raise ValueError("Malformed cup plan")
    tie_id = UUID(parts[1])
    leg = int(parts[3])
    tie = await session.get(CupTieModel, tie_id, with_for_update=True)
    bracket = await session.get(CupBracketModel, fixture.season_id)
    if tie is None or bracket is None or tie.season_id != fixture.season_id:
        raise ValueError("Unknown cup tie")
    graph = await frozen_bracket(session, bracket)
    definition = next((t for t in graph.ties if t.id == tie.id), None)
    if (
        definition is None
        or tie.round_key != definition.round_key
        or tie.neutral != definition.neutral
        or tie.cup_days != list(definition.cup_days)
        or (tie.home_kind, tie.home_tie_id) != (definition.home.kind, definition.home.tie_id)
        or (tie.away_kind, tie.away_tie_id) != (definition.away.kind, definition.away.tie_id)
    ):
        raise ValueError("Cup tie differs from frozen draw")
    all_ties = (
        await session.scalars(select(CupTieModel).where(CupTieModel.season_id == fixture.season_id))
    ).all()
    winners = {
        t.id: t.winner_club_id
        for t in all_ties
        if t.status == "decided" and t.winner_club_id is not None
    }
    validate_winners(graph, winners)
    from goaletto.domain.cup_bracket import resolve_slot

    a, b = (
        resolve_slot(graph, definition.home, winners),
        resolve_slot(graph, definition.away, winners),
    )
    if a is None or b is None or (a, b) != (tie.home_club_id, tie.away_club_id):
        raise ValueError("Cup participants have unresolved predecessors")
    if tie.status == "decided":
        raise ValueError("Cup tie already decided")
    if leg not in ((1,) if tie.neutral else (1, 2)):
        raise ValueError("Invalid cup leg")
    expected = (b, a) if leg == 2 else (a, b)
    season = await session.get(Season, fixture.season_id)
    if season is None or season.cycle_epoch is None:
        raise ValueError("Cup season has no epoch")
    day = season.cycle_epoch + timedelta(days=4 * (tie.cup_days[leg - 1] - 1))
    # Explicit calendar recovery may move an unannounced plan to a later cup
    # day. It keeps its structural round/leg and acquires a new revision.
    recovered = (
        plan.revision > 1
        and plan.assigned_day > day
        and (plan.assigned_day - season.cycle_epoch).days % 4 == 0
    )
    if (
        plan.id != uuid5(fixture.season_id, f"cup:{tie.id}:{leg}")
        or plan.season_id != fixture.season_id
        or plan.announced_at is None
        or (plan.assigned_day != day and not recovered)
        or plan.round_number != tie.cup_days[leg - 1]
        or fixture.round_number != plan.round_number
        or (plan.home_club_id, plan.away_club_id) != expected
        or (fixture.home_club_id, fixture.away_club_id) != expected
        or fixture.leg != leg
        or fixture.stage != tie.round_key
        or fixture.neutral != tie.neutral
        or plan.stage != fixture.stage
        or plan.leg != fixture.leg
        or plan.neutral != fixture.neutral
    ):
        raise ValueError("Cup fixture does not match its structural plan")
    if leg == 2 and tie.first_match_result is None:
        from goaletto.application.match_runtime import NotReady

        raise NotReady("Return leg waits for the first result")
    return KnockoutContext(
        tie.id,
        KnockoutTie(KNOCKOUT_POLICY, a, b, "single" if tie.neutral else "two-leg"),
        "final" if tie.neutral else "leg2" if leg == 2 else "leg1",
        _load_segment(tie.first_match_result) if leg == 2 and tie.first_match_result else None,
    )


async def record_cup_match_result(
    session: AsyncSession, fixture: Fixture, match: Match, at: datetime
) -> CupTieModel:
    require_utc(at)
    await allocation_lock(session)
    state = restore_state(match.state)
    k = state.knockout
    result = await session.get(ResultReceipt, match.id)
    if (
        k is None
        or state.engine_version != KNOCKOUT_ENGINE_VERSION
        or result is None
        or result.state_hash != state_hash(state)
    ):
        raise ValueError("Cup progression needs a genuine finished knockout")
    target = await session.get(CupTieModel, k.tie_id, with_for_update=True)
    if target is None or target.season_id != fixture.season_id:
        raise ValueError("Wrong cup tie result")
    if target.status == "decided":
        raise ValueError("Cup tie was already settled")
    outcome = decision(state)
    assert k.regulation is not None
    if k.segment == "leg1":
        target.first_match_result = _store_segment(k.regulation)
        target.status = "in_progress"
    else:
        if outcome.status != "decided" or outcome.winner is None:
            raise ValueError("Knockout result is unfinished")
        if k.segment == "final":
            target.first_match_result = _store_segment(k.regulation)
        else:
            assert k.first_match is not None
            if target.first_match_result != _store_segment(k.first_match):
                raise ValueError("First leg changed during the return match")
            target.second_match_result = _store_segment(k.regulation)
        if state.tick == 40:
            target.extra_time_result = _store_segment(
                MatchResult(
                    "final-et" if k.segment == "final" else "leg2-et",
                    fixture.home_club_id,
                    fixture.away_club_id,
                    state.home.stats.goals - k.regulation.home_goals,
                    state.away.stats.goals - k.regulation.away_goals,
                    True,
                )
            )
        if k.shootout:
            target.shootout_result = _dump(asdict(k.shootout))
        target.status, target.winner_club_id, target.decided_at = "decided", outcome.winner, at
        await session.flush()
        await advance_bracket_progression(session, fixture.season_id, target.id, outcome.winner, at)
        if fixture.neutral:
            await settle_final_revenue(session, fixture, at)
    await session.flush()
    return target


async def advance_bracket_progression(
    session: AsyncSession,
    season_id: UUID,
    tie_id: UUID,
    winner_id: UUID,
    at: datetime,
) -> None:
    """Populate winner into downstream ties and fixture plans; strip loser from candidate trees.

    AND-122: eliminated clubs must leave ``FixturePlan.possible_club_ids``.
    Every cup plan whose tie is decided keeps an empty candidate list, and
    the decided loser is pruned from every other cup plan of the season.
    Unknown opponents never acquire invented kickoff times: plans with an
    unresolved slot stay conditional until both participants are known.
    """
    all_ties = (
        await session.scalars(
            select(CupTieModel).where(CupTieModel.season_id == season_id).with_for_update()
        )
    ).all()

    bracket = await session.get(CupBracketModel, season_id)
    if bracket is None:
        raise ValueError("Missing frozen cup bracket")
    graph = await frozen_bracket(session, bracket)
    winners = {
        t.id: t.winner_club_id
        for t in all_ties
        if t.status == "decided" and t.winner_club_id is not None
    }
    validate_winners(graph, winners)
    if winners.get(tie_id) != winner_id:
        raise ValueError("Progression requires the persisted winner")

    # Update direct consumers of this tie
    for t in all_ties:
        if t.home_tie_id == tie_id:
            t.home_club_id = winner_id
        if t.away_tie_id == tie_id:
            t.away_club_id = winner_id

    tie_by_id_map = {t.id: t for t in all_ties}
    decided = {t.id: t for t in all_ties if t.status == "decided" and t.winner_club_id is not None}
    losers = {
        club_id
        for t in decided.values()
        for club_id in (t.home_club_id, t.away_club_id)
        if club_id is not None and club_id != t.winner_club_id
    }

    # Update FixturePlans
    plans = (
        await session.scalars(
            select(FixturePlan)
            .where(FixturePlan.season_id == season_id, FixturePlan.competition == "cup")
            .with_for_update()
        )
    ).all()

    # Update FixturePlans using conditional_ref to precisely match each plan to its tie+leg.
    # conditional_ref format: "tie:<tie_uuid>:leg:<leg_number>"
    for plan in plans:
        if plan.conditional_ref is None:
            continue
        parts = plan.conditional_ref.split(":")
        if len(parts) != 4 or parts[0] != "tie" or parts[2] != "leg":
            continue
        try:
            plan_tie_id = UUID(parts[1])
            plan_leg = int(parts[3])
        except (ValueError, AttributeError):
            continue

        downstream_tie = tie_by_id_map.get(plan_tie_id)
        if downstream_tie is None:
            continue

        home_id = downstream_tie.home_club_id
        away_id = downstream_tie.away_club_id
        if home_id is not None and away_id is not None:
            # Both slots resolved: materialise participants, drop candidates.
            if downstream_tie.neutral:
                # Single neutral match: home/away orientation as stored on the tie
                plan.home_club_id = home_id
                plan.away_club_id = away_id
            elif plan_leg == 1:
                # Leg 1: home side is the tie's home club (club_a)
                plan.home_club_id = home_id
                plan.away_club_id = away_id
            elif plan_leg == 2:
                # Leg 2: home/away are swapped (club_b hosts the return)
                plan.home_club_id = away_id
                plan.away_club_id = home_id
            plan.possible_club_ids = []
        else:
            # Still conditional: prune decided losers, keep the live candidates.
            plan.possible_club_ids = [
                club_id for club_id in plan.possible_club_ids if UUID(club_id) not in losers
            ]


async def pin_final_venue(
    session: AsyncSession,
    fixture: Fixture,
    home: Club,
    away: Club,
    seed: int,
    at: datetime,
    *,
    competition: str = "cup",
) -> Attendance:
    """Book a real third club's free stadium and freeze every financial input."""
    if (
        not fixture.neutral
        or fixture.competition != competition
        or competition not in ("cup", "champions")
    ):
        raise ValueError("Neutral venue requires a knockout final")
    day = fixture.scheduled_start_at.date()
    occupied = select(Fixture.home_club_id).where(
        Fixture.scheduled_start_at >= datetime.combine(day, datetime.min.time(), tzinfo=at.tzinfo),
        Fixture.scheduled_start_at
        < datetime.combine(day, datetime.min.time(), tzinfo=at.tzinfo) + timedelta(days=1),
        Fixture.status != "cancelled",
        Fixture.neutral.is_(False),
    )
    booked = select(NeutralFinalBooking.venue_club_id).where(
        NeutralFinalBooking.assigned_day == day
    )
    venue = await session.scalar(
        select(Club)
        .join(SeasonClub, SeasonClub.club_id == Club.id)
        .where(
            SeasonClub.season_id == fixture.season_id,
            Club.id.not_in((home.id, away.id)),
            Club.id.not_in(occupied),
            Club.id.not_in(booked),
            Club.stadium_capacity > 0,
        )
        .order_by(Club.stadium_capacity.desc(), Club.id)
        .limit(1)
        .with_for_update(of=Club)
    )
    if venue is None:
        raise ValueError("No free neutral stadium")
    snapshot = NeutralFinalSnapshot(
        NEUTRAL_FINAL_POLICY,
        venue.id,
        venue.stadium_capacity,
        CUP_FINAL_TICKET_PRICE,
        home.id,
        away.id,
        home.supporters,
        away.supporters,
        seed,
        True,
    )
    frozen = settle_neutral_final(snapshot)
    session.add(
        NeutralFinalBooking(
            fixture_id=fixture.id,
            venue_club_id=venue.id,
            venue_name=venue.name,
            assigned_day=day,
            locked_at=at,
            basis=_dump(asdict(frozen)),
        )
    )
    return Attendance(
        home.supporters + away.supporters,
        frozen.capacity,
        1,
        1,
        (),
        NEUTRAL_DEMAND_BPS,
        frozen.crowd,
    )


async def settle_final_revenue(
    session: AsyncSession, fixture: Fixture, at: datetime, *, competition: str = "cup"
) -> None:
    if competition not in ("cup", "champions"):
        raise ValueError("Unknown knockout competition")
    booking = await session.get(NeutralFinalBooking, fixture.id)
    if booking is None:
        raise ValueError("Final has no pinned neutral venue")
    basis = booking.basis
    frozen = settle_neutral_final(
        NeutralFinalSnapshot(
            NEUTRAL_FINAL_POLICY,
            UUID(basis["venue_id"]),
            basis["capacity"],
            basis["ticket_price"],
            UUID(basis["club_a_id"]),
            UUID(basis["club_b_id"]),
            basis["supporters_a"],
            basis["supporters_b"],
            basis["seed"],
            True,
        )
    )
    if _dump(asdict(frozen)) != basis or {frozen.club_a_id, frozen.club_b_id} != {
        fixture.home_club_id,
        fixture.away_club_id,
    }:
        raise ValueError("Neutral settlement differs from its pinned basis")
    accounts = await locked_accounts(session, frozen.club_a_id, frozen.club_b_id)
    for club_id, amount in (
        (frozen.club_a_id, frozen.payout_a),
        (frozen.club_b_id, frozen.payout_b),
    ):
        await credit_locked(
            session,
            accounts[club_id],
            key=f"{competition}:final:tickets:{fixture.id}:{club_id}",
            kind="tickets",
            amount=amount,
            at=at,
            details=basis,
        )
