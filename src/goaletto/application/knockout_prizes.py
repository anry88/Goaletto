"""Settle and reconcile prizes against immutable knockout results."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.application.game_actions import allocation_lock
from goaletto.application.money import credit_locked, locked_accounts, request
from goaletto.domain.champions_bracket import rounds_for
from goaletto.domain.clock import MatchPhase
from goaletto.domain.knockout_match import decision
from goaletto.domain.knockout_prizes import (
    CHAMPIONS_TIER_POLICY,
    CHAMPIONS_WINNER_PRIZE,
    CUP_EXIT_PRIZES,
    CUP_WINNER_PRIZE,
    POLICY,
    champions_exit_prize,
    champions_prize_multiplier,
)
from goaletto.domain.match_codec import restore_state, state_hash
from goaletto.infrastructure.models import (
    ChampionsTie,
    ChampionsTournament,
    CupTie,
    Fixture,
    FixturePlan,
    LedgerEntry,
    Match,
    MatchResult,
    Season,
)


@dataclass(frozen=True)
class Prize:
    club_id: UUID
    key: str
    amount: int
    details: dict[str, Any]


async def prizes_for_tie(
    session: AsyncSession, tie: CupTie | ChampionsTie, result: MatchResult
) -> list[Prize]:
    home, away, winner = tie.home_club_id, tie.away_club_id, tie.winner_club_id
    if (
        tie.status != "decided"
        or home is None
        or away is None
        or home == away
        or winner is None
        or winner not in (home, away)
        or result.season_id != tie.season_id
        or (result.home_club_id, result.away_club_id)
        != ((home, away) if tie.neutral else (away, home))
    ):
        raise ValueError("Prize participants differ from the decided tie")

    details: dict[str, Any] = {
        "policy": POLICY,
        "season_id": str(tie.season_id),
        "tie_id": str(tie.id),
        "fixture_id": str(result.fixture_id),
        "match_id": str(result.match_id),
        "state_hash": result.state_hash,
    }
    if isinstance(tie, CupTie):
        competition, edition = "cup", tie.season_id
        exit_prize, winner_prize = CUP_EXIT_PRIZES[tie.round_key], CUP_WINNER_PRIZE
        final = tie.round_key == "final"
        details["stage"] = tie.round_key
    else:
        tournament = await session.get(ChampionsTournament, tie.tournament_id)
        if tournament is None or tournament.season_id != tie.season_id:
            raise ValueError("Prize tie lacks its champions tournament")
        competition, edition = "champions", tournament.id
        multiplier = champions_prize_multiplier(tournament.tier)
        exit_prize = (
            champions_exit_prize(tournament.participant_count, tie.round_number) * multiplier
        )
        winner_prize = CHAMPIONS_WINNER_PRIZE * multiplier
        final = tie.round_number == rounds_for(tournament.participant_count)
        details.update(
            policy=CHAMPIONS_TIER_POLICY,
            round=tie.round_number,
            participants=tournament.participant_count,
            qualifying_tier=tournament.tier,
            tier_multiplier=multiplier,
        )
    if result.competition != competition or final != tie.neutral:
        raise ValueError("Prize competition or final does not reconcile")
    details.update(competition=competition, edition_id=str(edition))
    key = f"{competition}:prize:{edition}"
    loser = away if winner == home else home
    prizes = [Prize(loser, key, exit_prize, {**details, "placement": "eliminated"})]
    if final:
        prizes.append(Prize(winner, key, winner_prize, {**details, "placement": "champion"}))
    return prizes


def _matches_paid_prize(entry: LedgerEntry, prize: Prize) -> bool:
    if entry.kind != "prize":
        return False
    if entry.cash_delta == prize.amount and entry.request == request(
        "prize", prize.amount, prize.details
    ):
        return True
    if prize.details["policy"] != CHAMPIONS_TIER_POLICY:
        return False
    # Recognize the original flat prize under the same placement key.
    details = dict(prize.details, policy=POLICY)
    details.pop("qualifying_tier")
    base = prize.amount // details.pop("tier_multiplier")
    return entry.cash_delta == base and entry.request == request("prize", base, details)


async def pay_prizes(session: AsyncSession, prizes: list[Prize], at: datetime) -> None:
    if prizes:
        accounts = await locked_accounts(session, *{p.club_id for p in prizes})
        for prize in prizes:
            await credit_locked(
                session,
                accounts[prize.club_id],
                prize.key,
                "prize",
                prize.amount,
                at,
                prize.details,
            )


async def _deciding_result(
    session: AsyncSession, tie: CupTie | ChampionsTie, before: datetime
) -> MatchResult:
    competition, ref = ("cup", "tie") if isinstance(tie, CupTie) else ("champions", "champions-tie")
    leg = 1 if tie.neutral else 2
    fixture_id = uuid5(tie.season_id, f"{competition}:{tie.id}:{leg}")
    row = (
        (
            await session.execute(
                select(MatchResult, Match, Fixture)
                .join(Match, Match.id == MatchResult.match_id)
                .join(Fixture, Fixture.id == MatchResult.fixture_id)
                .join(FixturePlan, FixturePlan.id == Fixture.id)
                .where(
                    Fixture.id == fixture_id,
                    Fixture.status == "finished",
                    Fixture.season_id == tie.season_id,
                    Fixture.competition == competition,
                    Fixture.leg == leg,
                    Fixture.neutral == tie.neutral,
                    FixturePlan.season_id == tie.season_id,
                    FixturePlan.competition == competition,
                    FixturePlan.conditional_ref == f"{ref}:{tie.id}:leg:{leg}",
                    FixturePlan.announced_at.is_not(None),
                    MatchResult.actual_finish_at == Fixture.actual_finish_at,
                    MatchResult.actual_finish_at <= before,
                )
            )
        )
        .tuples()
        .one_or_none()
    )
    if row is None:
        raise ValueError(f"Decided tie {tie.id} lacks a finished official fixture")
    result, match, fixture = row
    state = restore_state(match.state)
    k = state.knockout
    if (
        k is None
        or k.tie_id != tie.id
        or k.segment != ("final" if tie.neutral else "leg2")
        or state.phase != MatchPhase.FULL_TIME
        or state.match_id != match.id
        or (state.home.snapshot.club_id, state.away.snapshot.club_id)
        != (result.home_club_id, result.away_club_id)
        or (fixture.home_club_id, fixture.away_club_id)
        != (result.home_club_id, result.away_club_id)
        or state_hash(state) != result.state_hash
        or decision(state).winner != tie.winner_club_id
    ):
        raise ValueError(f"Decided tie {tie.id} differs from its final checkpoint")
    return result


async def unpaid_current_prizes(session: AsyncSession, before: datetime) -> list[Prize]:
    """Plan additions for decided ties in editions that have not rolled over."""
    require_utc(before)
    planned: dict[tuple[UUID, str], Prize] = {}
    models: tuple[type[CupTie | ChampionsTie], ...] = (CupTie, ChampionsTie)
    for model in models:
        ties = await session.scalars(
            select(model)
            .join(Season, Season.id == model.season_id)
            .where(
                Season.status.in_(("active", "finished")),
                model.status == "decided",
                model.decided_at <= before,
            )
            .order_by(model.decided_at, model.id)
        )
        for row in ties:
            tie = cast(CupTie | ChampionsTie, row)
            result = await _deciding_result(session, tie, before)
            for prize in await prizes_for_tie(session, tie, result):
                identity = (prize.club_id, prize.key)
                if identity in planned:
                    raise ValueError("Club has multiple final placements in one edition")
                planned[identity] = prize
    if not planned:
        return []
    existing = await session.scalars(
        select(LedgerEntry).where(LedgerEntry.operation_key.in_({p.key for p in planned.values()}))
    )
    for entry in existing:
        prior_prize = planned.pop((entry.club_id, entry.operation_key), None)
        if prior_prize is not None and not _matches_paid_prize(entry, prior_prize):
            raise ValueError("Existing knockout prize differs from the final placement")
    return list(planned.values())


async def backfill_current_prizes(
    session: AsyncSession, before: datetime, at: datetime
) -> list[Prize]:
    require_utc(before)
    require_utc(at)
    if before > at:
        raise ValueError("Prize cutoff is in the future")
    await allocation_lock(session)
    prizes = await unpaid_current_prizes(session, before)
    await pay_prizes(session, prizes, at)
    return prizes
