"""Bounded read-only collectors: SELECT statements only.

This module is deliberately restricted:

* only ``select(...)`` statements executed on a read ``AsyncConnection``;
* no ``FOR UPDATE`` / ``SKIP LOCKED`` row captures, no advisory locks;
* no INSERT/UPDATE/DELETE, no session flush/commit, no event recording;
* only routing columns are selected — never ``OutboxMessage.payload`` in
  full, never ``Match.state``, never chat IDs, message IDs, tokens or DSNs.

Every query is bounded with ``LIMIT``. When a scope hits its bound the
snapshot records the scope name in ``incomplete_scopes`` so that checks
requiring full coverage suppress their findings instead of declaring
partial coverage proof of damage.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Executable, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncConnection

from goaletto.diagnostics.snapshot import (
    AppearanceStock,
    AttemptInfo,
    AuctionRow,
    BidRow,
    BoardRow,
    FinanceAccountRow,
    FixtureRow,
    JobRow,
    MatchRow,
    OutboxRow,
    RecoveryRow,
    ReservationRow,
    ResultRow,
    SeasonRow,
    Snapshot,
)
from goaletto.infrastructure.models import (
    AppearanceIdentity,
    Auction,
    AuctionBid,
    Club,
    FinanceAccount,
    Fixture,
    Job,
    Match,
    MatchRecovery,
    MatchResult,
    MatchStage,
    MatchTick,
    MoneyReservation,
    OutboxBoard,
    OutboxDeliveryAttempt,
    OutboxMessage,
    Person,
    Season,
)

_JOB_EXTRACT_LABELS = ("season_id", "fixture_id", "tick_number")


def _parse_uuid(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None


def _parse_tick(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value if 1 <= value <= 30 else None
    if isinstance(value, str):
        try:
            tick = int(value)
        except ValueError:
            return None
        return tick if 1 <= tick <= 30 else None
    return None


async def _fetch_limited(
    connection: AsyncConnection, statement: Executable, limit: int
) -> tuple[list[Any], bool]:
    rows = (await connection.execute(statement)).all()
    truncated = len(rows) > limit
    return list(rows[:limit]), truncated


async def _collect_jobs(
    connection: AsyncConnection, limit: int, season_id: UUID | None
) -> tuple[list[JobRow], bool]:
    statement = (
        select(
            Job.id,
            Job.kind,
            Job.status,
            Job.due_at,
            Job.attempts,
            Job.last_error,
            Job.payload["season_id"].astext.label("season_id"),
            Job.payload["fixture_id"].astext.label("fixture_id"),
            Job.payload["tick_number"].astext.label("tick_number"),
            Job.payload["club_id"].astext.label("club_id"),
            Job.payload["auction_id"].astext.label("auction_id"),
            Job.payload["plan_id"].astext.label("plan_id"),
            Job.payload["expansion_id"].astext.label("expansion_id"),
            Job.payload["person_id"].astext.label("person_id"),
            Job.payload["ownership_id"].astext.label("ownership_id"),
        )
        .order_by(Job.due_at.asc(), Job.id.asc())
        .limit(limit + 1)
    )
    if season_id is not None:
        statement = statement.where(Job.payload["season_id"].astext == str(season_id))
    raw, truncated = await _fetch_limited(connection, statement, limit)
    rows = [
        JobRow(
            id=row[0],
            kind=str(row[1]),
            status=str(row[2]),
            due_at=row[3],
            attempts=int(row[4]),
            last_error=None if row[5] is None else str(row[5]),
            season_id=_parse_uuid(row[6]),
            fixture_id=_parse_uuid(row[7]),
            tick_number=_parse_tick(row[8]),
            club_id=_parse_uuid(row[9]),
            auction_id=_parse_uuid(row[10]),
            plan_id=_parse_uuid(row[11]),
            expansion_id=_parse_uuid(row[12]),
            person_id=_parse_uuid(row[13]),
            ownership_id=_parse_uuid(row[14]),
        )
        for row in raw
    ]
    return rows, truncated


async def _collect_outbox(connection: AsyncConnection, limit: int) -> tuple[list[OutboxRow], bool]:
    statement = (
        select(
            OutboxMessage.id,
            OutboxMessage.status,
            OutboxMessage.due_at,
            OutboxMessage.attempts,
            OutboxMessage.last_error,
            OutboxMessage.leased_until,
            OutboxMessage.payload["kind"].astext.label("kind"),
            OutboxMessage.live_board_key,
        )
        .order_by(OutboxMessage.due_at.asc(), OutboxMessage.id.asc())
        .limit(limit + 1)
    )
    raw, truncated = await _fetch_limited(connection, statement, limit)
    return (
        [
            OutboxRow(
                id=row[0],
                status=str(row[1]),
                due_at=row[2],
                attempts=int(row[3]),
                last_error=None if row[4] is None else str(row[4]),
                leased_until=row[5],
                kind=None if row[6] is None else str(row[6]),
                stream_key=None if row[7] is None else str(row[7]),
            )
            for row in raw
        ],
        truncated,
    )


async def _collect_boards(connection: AsyncConnection, limit: int) -> tuple[list[BoardRow], bool]:
    statement = (
        select(
            OutboxBoard.key,
            OutboxBoard.fixture_id,
            OutboxBoard.ownership_id,
            OutboxBoard.leased_until,
            OutboxBoard.retry_at,
            OutboxBoard.revision,
        )
        .order_by(OutboxBoard.key.asc())
        .limit(limit + 1)
    )
    raw, truncated = await _fetch_limited(connection, statement, limit)
    return (
        [
            BoardRow(
                key=str(row[0]),
                fixture_id=_parse_uuid(row[1]),
                ownership_id=_parse_uuid(row[2]),
                leased_until=row[3],
                retry_at=row[4],
                revision=int(row[5]),
            )
            for row in raw
        ],
        truncated,
    )


async def _collect_attempts(
    connection: AsyncConnection, limit: int
) -> tuple[list[AttemptInfo], bool]:
    statement = (
        select(
            OutboxDeliveryAttempt.outbox_id,
            OutboxDeliveryAttempt.attempt,
            OutboxDeliveryAttempt.outcome,
        )
        .order_by(OutboxDeliveryAttempt.outbox_id.asc(), OutboxDeliveryAttempt.attempt.desc())
        .limit(limit + 1)
    )
    raw, truncated = await _fetch_limited(connection, statement, limit)
    latest: dict[UUID, AttemptInfo] = {}
    for row in raw:
        outbox_id = row[0]
        if not isinstance(outbox_id, UUID) or outbox_id in latest:
            continue
        latest[outbox_id] = AttemptInfo(
            outbox_id=outbox_id, attempt=int(row[1]), outcome=str(row[2])
        )
    return list(latest.values()), truncated


async def _collect_fixtures(
    connection: AsyncConnection, limit: int, season_id: UUID | None
) -> tuple[list[FixtureRow], set[UUID], bool]:
    statement = (
        select(
            Fixture.id,
            Fixture.season_id,
            Fixture.status,
            Fixture.scheduled_start_at,
            Fixture.lineup_lock_at,
            Fixture.actual_start_at,
            Fixture.actual_finish_at,
            Fixture.home_club_id,
            Fixture.away_club_id,
        )
        .order_by(Fixture.scheduled_start_at.asc(), Fixture.id.asc())
        .limit(limit + 1)
    )
    if season_id is not None:
        statement = statement.where(Fixture.season_id == season_id)
    raw, truncated = await _fetch_limited(connection, statement, limit)
    rows = [
        FixtureRow(
            id=row[0],
            season_id=row[1],
            status=str(row[2]),
            scheduled_start_at=row[3],
            lineup_lock_at=row[4],
            actual_start_at=row[5],
            actual_finish_at=row[6],
            home_club_id=row[7] if isinstance(row[7], UUID) else None,
            away_club_id=row[8] if isinstance(row[8], UUID) else None,
        )
        for row in raw
    ]
    return rows, {row.id for row in rows}, truncated


async def _collect_matches(
    connection: AsyncConnection,
    limit: int,
    fixture_ids: set[UUID] | None,
    tick_limit: int,
) -> tuple[list[MatchRow], bool, bool]:
    statement = (
        select(
            Match.id,
            Match.fixture_id,
            Match.phase,
            Match.completed_tick,
            Match.next_due_at,
            Match.paused_at,
            Match.paused_seconds,
        )
        .order_by(Match.fixture_id.asc())
        .limit(limit + 1)
    )
    if fixture_ids is not None:
        statement = statement.where(Match.fixture_id.in_(sorted(fixture_ids)))
    raw, truncated = await _fetch_limited(connection, statement, limit)
    wanted = {row[0] for row in raw if isinstance(row[0], UUID)}
    tick_statement = (
        select(MatchTick.match_id, MatchTick.tick_number)
        .order_by(MatchTick.match_id.asc(), MatchTick.tick_number.asc())
        .limit(tick_limit + 1)
    )
    tick_statement = tick_statement.where(MatchTick.match_id.in_(sorted(wanted)))
    tick_raw, ticks_truncated = await _fetch_limited(connection, tick_statement, tick_limit)
    ticks_by_match: dict[UUID, list[int]] = {}
    for match_id, tick_number in tick_raw:
        if isinstance(match_id, UUID) and isinstance(tick_number, int):
            ticks_by_match.setdefault(match_id, []).append(tick_number)
    rows = [
        MatchRow(
            id=row[0],
            fixture_id=row[1],
            phase=str(row[2]),
            completed_tick=int(row[3]),
            next_due_at=row[4],
            tick_numbers=tuple(ticks_by_match.get(row[0], ())),
            paused_at=row[5],
            paused_seconds=int(row[6]) if isinstance(row[6], int) else 0,
        )
        for row in raw
        if isinstance(row[0], UUID) and isinstance(row[1], UUID)
    ]
    return rows, truncated, ticks_truncated


async def _collect_results(
    connection: AsyncConnection, limit: int, fixture_ids: set[UUID] | None
) -> tuple[list[ResultRow], bool]:
    statement = (
        select(MatchResult.match_id, MatchResult.fixture_id)
        .order_by(MatchResult.fixture_id.asc())
        .limit(limit + 1)
    )
    if fixture_ids is not None:
        statement = statement.where(MatchResult.fixture_id.in_(sorted(fixture_ids)))
    raw, truncated = await _fetch_limited(connection, statement, limit)
    return (
        [
            ResultRow(match_id=row[0], fixture_id=row[1])
            for row in raw
            if isinstance(row[0], UUID) and isinstance(row[1], UUID)
        ],
        truncated,
    )


async def _collect_seasons(
    connection: AsyncConnection, season_id: UUID | None, limit: int
) -> tuple[list[SeasonRow], bool]:
    statement = select(Season.id, Season.status).order_by(Season.id.asc()).limit(limit + 1)
    if season_id is not None:
        statement = statement.where(Season.id == season_id)
    raw, truncated = await _fetch_limited(connection, statement, limit)
    return (
        [SeasonRow(id=row[0], status=str(row[1])) for row in raw if isinstance(row[0], UUID)],
        truncated,
    )


async def _collect_recoveries(
    connection: AsyncConnection, limit: int, fixture_ids: set[UUID] | None
) -> tuple[list[RecoveryRow], bool]:
    statement = (
        select(
            MatchRecovery.id,
            MatchRecovery.match_id,
            Match.fixture_id,
            MatchRecovery.checkpoint_tick,
            MatchRecovery.state_hash,
            MatchRecovery.previous_due_at,
            MatchRecovery.next_due_at,
            MatchRecovery.recovered_at,
            MatchRecovery.reason,
            MatchTick.state_hash,
            MatchStage.state_hash,
        )
        .select_from(MatchRecovery)
        .outerjoin(Match, Match.id == MatchRecovery.match_id)
        .outerjoin(
            MatchTick,
            and_(
                MatchTick.match_id == MatchRecovery.match_id,
                MatchTick.tick_number == MatchRecovery.checkpoint_tick,
            ),
        )
        .outerjoin(
            MatchStage,
            and_(
                MatchStage.match_id == MatchRecovery.match_id,
                or_(
                    and_(MatchRecovery.checkpoint_tick == 0, MatchStage.stage == "locked"),
                    and_(MatchRecovery.checkpoint_tick == 15, MatchStage.stage == "second_half"),
                ),
            ),
        )
        .order_by(MatchRecovery.recovered_at.asc(), MatchRecovery.id.asc())
        .limit(limit + 1)
    )
    if fixture_ids is not None:
        statement = statement.where(
            (Match.fixture_id.in_(sorted(fixture_ids))) | (Match.fixture_id.is_(None))
        )
    raw, truncated = await _fetch_limited(connection, statement, limit)
    rows = [
        RecoveryRow(
            id=row[0],
            match_id=row[1],
            fixture_id=row[2] if isinstance(row[2], UUID) else None,
            checkpoint_tick=int(row[3]),
            state_hash=str(row[4]),
            previous_due_at=row[5],
            next_due_at=row[6],
            recovered_at=row[7],
            reason=str(row[8]),
            checkpoint_hashes=tuple(h for h in (row[9], row[10]) if isinstance(h, str)),
        )
        for row in raw
        if isinstance(row[0], UUID) and isinstance(row[1], UUID)
    ]
    return rows, truncated


async def _collect_finance(
    connection: AsyncConnection, limit: int
) -> tuple[list[FinanceAccountRow], bool]:
    statement = (
        select(Club.id, Club.budget, FinanceAccount.reserved, FinanceAccount.arrears)
        .join(FinanceAccount, FinanceAccount.club_id == Club.id)
        .order_by(Club.id.asc())
        .limit(limit + 1)
    )
    raw, truncated = await _fetch_limited(connection, statement, limit)
    rows = [
        FinanceAccountRow(
            club_id=row[0],
            cash=int(row[1]),
            reserved=int(row[2]),
            arrears=int(row[3]),
        )
        for row in raw
        if isinstance(row[0], UUID)
    ]
    return rows, truncated


async def _collect_reservations(
    connection: AsyncConnection, limit: int
) -> tuple[list[ReservationRow], bool]:
    statement = (
        select(
            MoneyReservation.id,
            MoneyReservation.club_id,
            MoneyReservation.amount,
            MoneyReservation.status,
        )
        .order_by(MoneyReservation.club_id.asc(), MoneyReservation.id.asc())
        .limit(limit + 1)
    )
    raw, truncated = await _fetch_limited(connection, statement, limit)
    rows = [
        ReservationRow(id=row[0], club_id=row[1], amount=int(row[2]), status=str(row[3]))
        for row in raw
        if isinstance(row[0], UUID) and isinstance(row[1], UUID)
    ]
    return rows, truncated


async def _collect_auctions(
    connection: AsyncConnection, limit: int
) -> tuple[list[AuctionRow], bool]:
    statement = (
        select(
            Auction.id,
            Auction.seller_club_id,
            Auction.status,
            Auction.ends_at,
            Auction.highest_bid_id,
        )
        .order_by(Auction.ends_at.asc(), Auction.id.asc())
        .limit(limit + 1)
    )
    raw, truncated = await _fetch_limited(connection, statement, limit)
    rows = [
        AuctionRow(
            id=row[0],
            seller_club_id=row[1],
            status=str(row[2]),
            ends_at=row[3],
            highest_bid_id=row[4] if isinstance(row[4], UUID) else None,
        )
        for row in raw
        if isinstance(row[0], UUID) and isinstance(row[1], UUID)
    ]
    return rows, truncated


async def _collect_bids(connection: AsyncConnection, limit: int) -> tuple[list[BidRow], bool]:
    statement = (
        select(
            AuctionBid.id,
            AuctionBid.auction_id,
            AuctionBid.buyer_club_id,
            AuctionBid.amount,
            AuctionBid.reservation_id,
        )
        .order_by(AuctionBid.auction_id.asc(), AuctionBid.id.asc())
        .limit(limit + 1)
    )
    raw, truncated = await _fetch_limited(connection, statement, limit)
    rows = [
        BidRow(
            id=row[0],
            auction_id=row[1],
            buyer_club_id=row[2],
            amount=int(row[3]),
            reservation_id=row[4],
        )
        for row in raw
        if isinstance(row[0], UUID) and isinstance(row[1], UUID) and isinstance(row[4], UUID)
    ]
    return rows, truncated


async def _collect_appearance(connection: AsyncConnection) -> tuple[AppearanceStock, ...]:
    totals = (
        await connection.execute(
            select(AppearanceIdentity.origin_bank, func.count()).group_by(
                AppearanceIdentity.origin_bank
            )
        )
    ).all()
    used = (
        await connection.execute(
            select(AppearanceIdentity.origin_bank, func.count(func.distinct(AppearanceIdentity.id)))
            .join(Person, Person.appearance_identity_id == AppearanceIdentity.id)
            .group_by(AppearanceIdentity.origin_bank)
        )
    ).all()
    total_by_bank = {str(bank): int(count) for bank, count in totals}
    used_by_bank = {str(bank): int(count) for bank, count in used}
    stocks = []
    for bank in sorted(set(total_by_bank) | set(used_by_bank)):
        total = total_by_bank.get(bank, 0)
        taken = used_by_bank.get(bank, 0)
        stocks.append(
            AppearanceStock(bank=bank, total=total, used=taken, free=max(total - taken, 0))
        )
    return tuple(stocks)


async def collect_snapshot(
    connection: AsyncConnection,
    *,
    now: datetime,
    limit: int = 2000,
    season_id: UUID | None = None,
) -> Snapshot:
    """Run every bounded SELECT on one read connection and build a snapshot.

    The caller owns the connection; this function never commits, rolls back,
    or writes. ``season_id`` scopes calendar scopes (jobs, fixtures, matches,
    ticks, results, seasons, recoveries) to one season; the sender, finance
    and market scopes stay global.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware UTC")
    if limit < 1:
        raise ValueError("limit must be a positive int")
    jobs, jobs_truncated = await _collect_jobs(connection, limit, season_id)
    outbox, outbox_truncated = await _collect_outbox(connection, limit)
    boards, boards_truncated = await _collect_boards(connection, limit)
    attempts, attempts_truncated = await _collect_attempts(connection, limit)
    fixtures, fixture_ids, fixtures_truncated = await _collect_fixtures(
        connection, limit, season_id
    )
    matches, matches_truncated, ticks_truncated = await _collect_matches(
        connection, limit, fixture_ids if season_id is not None else None, limit * 10
    )
    results, results_truncated = await _collect_results(
        connection, limit, fixture_ids if season_id is not None else None
    )
    seasons, seasons_truncated = await _collect_seasons(connection, season_id, limit)
    recoveries, recoveries_truncated = await _collect_recoveries(
        connection, limit, fixture_ids if season_id is not None else None
    )
    finance_accounts, finance_truncated = await _collect_finance(connection, limit)
    reservations, reservations_truncated = await _collect_reservations(connection, limit)
    auctions, auctions_truncated = await _collect_auctions(connection, limit)
    bids, bids_truncated = await _collect_bids(connection, limit)
    appearance = await _collect_appearance(connection)
    incomplete = tuple(
        sorted(
            scope
            for scope, flag in (
                ("jobs", jobs_truncated),
                ("outbox", outbox_truncated),
                ("boards", boards_truncated),
                ("attempts", attempts_truncated),
                ("fixtures", fixtures_truncated),
                ("matches", matches_truncated),
                ("match_ticks", ticks_truncated),
                ("results", results_truncated),
                ("seasons", seasons_truncated),
                ("recoveries", recoveries_truncated),
                ("finance_accounts", finance_truncated),
                ("reservations", reservations_truncated),
                ("auctions", auctions_truncated),
                ("bids", bids_truncated),
            )
            if flag
        )
    )
    return Snapshot(
        now=now,
        jobs=tuple(jobs),
        jobs_truncated=jobs_truncated,
        outbox=tuple(outbox),
        outbox_truncated=outbox_truncated,
        boards=tuple(boards),
        boards_truncated=boards_truncated,
        attempts=tuple(attempts),
        attempts_truncated=attempts_truncated,
        fixtures=tuple(fixtures),
        fixtures_truncated=fixtures_truncated,
        matches=tuple(matches),
        matches_truncated=matches_truncated,
        ticks_truncated=ticks_truncated,
        results=tuple(results),
        results_truncated=results_truncated,
        seasons=tuple(seasons),
        seasons_truncated=seasons_truncated,
        appearance=appearance,
        recoveries=tuple(recoveries),
        recoveries_truncated=recoveries_truncated,
        finance_accounts=tuple(finance_accounts),
        finance_truncated=finance_truncated,
        reservations=tuple(reservations),
        reservations_truncated=reservations_truncated,
        auctions=tuple(auctions),
        auctions_truncated=auctions_truncated,
        market_schedule_complete=season_id is None,
        bids=tuple(bids),
        bids_truncated=bids_truncated,
        incomplete_scopes=incomplete,
    )


__all__ = ["collect_snapshot"]
