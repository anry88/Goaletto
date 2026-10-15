"""Durable 50-club cup bracket persistence, progression and neutral settlement.

Implements AND-123 (Cup bracket orchestration), AND-124 (knockout tie runtime
with extra time and penalties), and AND-125 (neutral final revenue settlement).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from random import Random
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.application.game_actions import allocation_lock
from goaletto.application.money import credit_locked, locked_accounts
from goaletto.application.training import reserve_match_day
from goaletto.domain.cup_bracket import (
    CupEntry,
    all_matches,
    build_bracket,
    possible_clubs,
)
from goaletto.domain.knockout import (
    KNOCKOUT_POLICY,
    KnockoutTie,
    MatchResult,
    PenaltyKick,
    PenaltyShootout,
    apply_penalty_kick,
    decide_shootout,
    decide_tie,
)
from goaletto.domain.neutral_final import (
    NEUTRAL_FINAL_POLICY,
    NeutralFinalSnapshot,
    settle_neutral_final,
)
from goaletto.domain.world import stable_seed
from goaletto.infrastructure.models import CupBracket as CupBracketModel
from goaletto.infrastructure.models import (
    CupTie as CupTieModel,
)
from goaletto.infrastructure.models import (
    Fixture,
    FixturePlan,
    League,
    Match,
    Season,
    SeasonClub,
)

CUP_DRAW_POLICY = "cup-draw-v1"
"""Seeded tiebreak draws for extra time and penalties.

Regulation stays 30 ticks through the shared match engine. When a cup tie
needs extra time or penalties, this runtime resolves them with a persisted
seeded draw (``stable_seed`` over the match seed) instead of playing extra
ticks: the 10-tick extra-time runtime with live commands is future work
(see ``docs/knockout-domain.md`` open integration points). Draws are
deterministic per match seed, stored in ``cup_ties`` and never redrawn on
retry (finalization is guarded by the ``MatchResult`` receipt).
"""

CUP_FINAL_TICKET_PRICE = 5
"""Neutral-final ticket price, reusing league ticket economics.

``domain.finances.ticket_income`` charges 5 per spectator; the final reuses
that price until the user approves dedicated final pricing. Venue capacity is
frozen from the finalists' stadiums (see ``settle_final_revenue``).
"""


def _seed_random(seed: int, label: str) -> Random:
    return Random(stable_seed(seed, label))


def _dump(value: Any) -> Any:
    """Convert dataclass payloads to JSONB-safe structures (UUIDs to strings)."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_dump(item) for item in value]
    return value


def _extra_time_goals(rng: Random, home_side: bool) -> int:
    """Single-draw extra-time goals for one side over 10 ticks."""
    draw = rng.random()
    if home_side:
        if draw < 0.684:
            return 0
        if draw < 0.964:
            return 1
        return 2
    if draw < 0.73:
        return 0
    if draw < 0.97:
        return 1
    return 2


def simulate_extra_time(
    home_id: UUID,
    away_id: UUID,
    segment: str,
    seed: int,
) -> MatchResult:
    """Deterministic extra-time goal resolution for 10 ticks."""
    rng = _seed_random(seed, f"extra-time:{segment}:{home_id}:{away_id}")
    # Single draw per side: mostly quiet, sometimes one goal, rarely two.
    return MatchResult(
        segment=segment,  # type: ignore[arg-type]
        home_id=home_id,
        away_id=away_id,
        home_goals=_extra_time_goals(rng, True),
        away_goals=_extra_time_goals(rng, False),
        completed=True,
    )


def _store_segment(result: MatchResult) -> dict[str, Any]:
    """Persist one pinned knockout segment JSONB-safe with the draw policy."""
    payload = _dump(asdict(result))
    assert isinstance(payload, dict)
    payload["draw_policy"] = CUP_DRAW_POLICY
    return payload


def _load_segment(payload: dict[str, Any]) -> MatchResult:
    """Rebuild a pinned knockout segment stored by :func:`_store_segment`."""
    data = dict(payload)
    data.pop("draw_policy", None)
    return MatchResult(
        segment=data["segment"],
        home_id=UUID(str(data["home_id"])),
        away_id=UUID(str(data["away_id"])),
        home_goals=int(data["home_goals"]),
        away_goals=int(data["away_goals"]),
        completed=bool(data["completed"]),
    )


def _store_shootout(shootout: PenaltyShootout) -> dict[str, Any]:
    """Persist the immutable penalty transcript JSONB-safe."""
    payload = _dump(asdict(shootout))
    assert isinstance(payload, dict)
    payload["draw_policy"] = CUP_DRAW_POLICY
    return payload


def simulate_shootout(
    club_a: UUID,
    club_b: UUID,
    seed: int,
) -> tuple[PenaltyShootout, dict[str, Any]]:
    """Deterministic penalty shootout resolution (5 initial kicks then sudden death)."""
    rng = _seed_random(seed, f"shootout:{club_a}:{club_b}")
    first_kicker = club_a if rng.random() < 0.5 else club_b
    shootout = PenaltyShootout(
        club_a_id=club_a, club_b_id=club_b, first_kicker_id=first_kicker, kicks=()
    )

    kick_number = 1
    current_kicker = first_kicker
    other_kicker = club_b if first_kicker == club_a else club_a

    while True:
        # Standard ~75% penalty conversion rate
        scored = rng.random() < 0.75
        kick = PenaltyKick(kick_number=kick_number, club_id=current_kicker, scored=scored)
        shootout = apply_penalty_kick(shootout, kick)
        result = decide_shootout(shootout)
        if result.status == "decided":
            return shootout, _store_shootout(shootout)
        kick_number += 1
        current_kicker, other_kicker = other_kicker, current_kicker


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


async def record_cup_match_result(
    session: AsyncSession,
    fixture: Fixture,
    match: Match,
    at: datetime,
) -> CupTieModel:
    """Record leg result, decide tie progression, run extra time/pens and settle neutral final."""
    require_utc(at)
    await allocation_lock(session)

    if fixture.competition != "cup":
        raise ValueError("Expected cup fixture")

    # Fixtures materialized from cup plans keep the plan id, so the tie and
    # leg resolve exactly through the plan's conditional_ref
    # ("tie:<tie_uuid>:leg:<leg_number>") instead of fuzzy club matching.
    plan = await session.get(FixturePlan, fixture.id)
    if plan is None or plan.competition != "cup" or plan.conditional_ref is None:
        raise ValueError(f"Cup fixture {fixture.id} has no cup plan")
    parts = plan.conditional_ref.split(":")
    if len(parts) != 4 or parts[0] != "tie" or parts[2] != "leg":
        raise ValueError(f"Cup plan {plan.id} has a malformed conditional ref")
    try:
        tie_id = UUID(parts[1])
        plan_leg = int(parts[3])
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Cup plan {plan.id} has a malformed conditional ref") from exc
    if plan_leg != fixture.leg:
        raise ValueError("Cup fixture leg differs from its plan")

    target_tie = await session.get(CupTieModel, tie_id, with_for_update=True)
    if target_tie is None or target_tie.season_id != fixture.season_id:
        raise ValueError(f"No matching cup tie found for fixture {fixture.id}")
    if target_tie.round_key != fixture.stage:
        raise ValueError("Cup fixture stage differs from its tie")
    if target_tie.status == "decided":
        # Finalization is guarded by the MatchResult receipt, so reaching a
        # decided tie means a replay: keep the pinned outcome, never redraw.
        return target_tie
    if target_tie.home_club_id is None or target_tie.away_club_id is None:
        raise ValueError("Cup tie participants are not resolved yet")

    club_a_id = target_tie.home_club_id
    club_b_id = target_tie.away_club_id
    if target_tie.neutral or fixture.neutral:
        expected = (club_a_id, club_b_id)
    elif fixture.leg == 1:
        expected = (club_a_id, club_b_id)
    elif fixture.leg == 2:
        expected = (club_b_id, club_a_id)
    else:
        raise ValueError("Cup fixture leg must be 1 or 2")
    if (fixture.home_club_id, fixture.away_club_id) != expected:
        raise ValueError("Cup fixture clubs differ from their tie orientation")

    domain_tie = KnockoutTie(
        policy_version=KNOCKOUT_POLICY,
        club_a_id=club_a_id,
        club_b_id=club_b_id,
        tie_format="single" if target_tie.neutral else "two-leg",
    )

    if fixture.neutral or target_tie.neutral:
        # Neutral Single Final
        final_match = MatchResult(
            segment="final",
            home_id=fixture.home_club_id,
            away_id=fixture.away_club_id,
            home_goals=match.home_goals,
            away_goals=match.away_goals,
            completed=True,
        )
        target_tie.first_match_result = _store_segment(final_match)
        decision = decide_tie(tie=domain_tie, first_match=final_match)

        extra_result = None
        if decision.status == "await_extra_time":
            extra_result = simulate_extra_time(
                fixture.home_club_id, fixture.away_club_id, "final-et", match.seed
            )
            target_tie.extra_time_result = _store_segment(extra_result)
            decision = decide_tie(tie=domain_tie, first_match=final_match, extra_time=extra_result)

        if decision.status == "await_penalties":
            shootout, shootout_res = simulate_shootout(
                domain_tie.club_a_id, domain_tie.club_b_id, match.seed
            )
            target_tie.shootout_result = shootout_res
            decision = decide_tie(
                tie=domain_tie,
                first_match=final_match,
                extra_time=extra_result,
                shootout=shootout,
            )

        if decision.status == "decided" and decision.winner is not None:
            target_tie.status = "decided"
            target_tie.winner_club_id = decision.winner
            target_tie.decided_at = at

            # Settle neutral final ticket revenue (AND-125)
            await settle_final_revenue(
                session,
                fixture=fixture,
                club_a_id=domain_tie.club_a_id,
                club_b_id=domain_tie.club_b_id,
                seed=match.seed,
                at=at,
            )

    elif fixture.leg == 1:
        leg1_match = MatchResult(
            segment="leg1",
            home_id=fixture.home_club_id,
            away_id=fixture.away_club_id,
            home_goals=match.home_goals,
            away_goals=match.away_goals,
            completed=True,
        )
        target_tie.first_match_result = _store_segment(leg1_match)
        target_tie.status = "in_progress"
        decision = decide_tie(tie=domain_tie, first_match=leg1_match)

    elif fixture.leg == 2:
        if target_tie.first_match_result is None:
            raise ValueError("Leg 2 cannot settle before leg 1")
        leg1_match = _load_segment(dict(target_tie.first_match_result))
        leg2_match = MatchResult(
            segment="leg2",
            home_id=fixture.home_club_id,
            away_id=fixture.away_club_id,
            home_goals=match.home_goals,
            away_goals=match.away_goals,
            completed=True,
        )
        target_tie.second_match_result = _store_segment(leg2_match)
        decision = decide_tie(tie=domain_tie, first_match=leg1_match, second_match=leg2_match)

        extra_result = None
        if decision.status == "await_extra_time":
            extra_result = simulate_extra_time(
                fixture.home_club_id, fixture.away_club_id, "leg2-et", match.seed
            )
            target_tie.extra_time_result = _store_segment(extra_result)
            decision = decide_tie(
                tie=domain_tie,
                first_match=leg1_match,
                second_match=leg2_match,
                extra_time=extra_result,
            )

        if decision.status == "await_penalties":
            shootout, shootout_res = simulate_shootout(
                domain_tie.club_a_id, domain_tie.club_b_id, match.seed
            )
            target_tie.shootout_result = shootout_res
            decision = decide_tie(
                tie=domain_tie,
                first_match=leg1_match,
                second_match=leg2_match,
                extra_time=extra_result,
                shootout=shootout,
            )

        if decision.status == "decided" and decision.winner is not None:
            target_tie.status = "decided"
            target_tie.winner_club_id = decision.winner
            target_tie.decided_at = at

            # Advance tournament progression
            await advance_bracket_progression(
                session,
                season_id=fixture.season_id,
                tie_id=target_tie.id,
                winner_id=decision.winner,
                at=at,
            )

    await session.flush()
    return target_tie


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


async def settle_final_revenue(
    session: AsyncSession,
    fixture: Fixture,
    club_a_id: UUID,
    club_b_id: UUID,
    seed: int,
    at: datetime,
) -> None:
    """Equal neutral-final ticket split with deterministic odd penny (AND-125).

    The venue is a frozen neutral arena id; capacity reuses the larger
    finalist stadium and the ticket price reuses league economics until the
    user approves dedicated final pricing and arena booking. Both inputs are
    frozen in the persisted snapshot inputs below (supporters, capacity,
    price, seed), so retries replay the same split.
    """
    accounts = await locked_accounts(session, club_a_id, club_b_id)
    supporters_a = accounts[club_a_id].club.supporters or 2500
    supporters_b = accounts[club_b_id].club.supporters or 2500

    venue_id = uuid5(fixture.season_id, "venue:neutral-arena")
    capacity = max(
        accounts[club_a_id].club.stadium_capacity or 0,
        accounts[club_b_id].club.stadium_capacity or 0,
    )
    if capacity <= 0:
        raise ValueError("Neutral final needs a positive venue capacity")
    snapshot = NeutralFinalSnapshot(
        venue_id=venue_id,
        capacity=capacity,
        ticket_price=CUP_FINAL_TICKET_PRICE,
        club_a_id=club_a_id,
        club_b_id=club_b_id,
        supporters_a=supporters_a,
        supporters_b=supporters_b,
        policy_version=NEUTRAL_FINAL_POLICY,
        seed=seed,
        neutral=True,
    )
    result = settle_neutral_final(snapshot)

    # Atomically credit both clubs in the same transaction
    await credit_locked(
        session,
        accounts[club_a_id],
        key=f"cup:final:tickets:{fixture.id}:{club_a_id}",
        kind="tickets",
        amount=result.payout_a,
        at=at,
        details={"crowd": result.crowd, "gross": result.gross, "venue_id": str(venue_id)},
    )
    await credit_locked(
        session,
        accounts[club_b_id],
        key=f"cup:final:tickets:{fixture.id}:{club_b_id}",
        kind="tickets",
        amount=result.payout_b,
        at=at,
        details={"crowd": result.crowd, "gross": result.gross, "venue_id": str(venue_id)},
    )
