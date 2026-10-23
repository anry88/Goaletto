"""Durable tournament cautions and bans. Caller holds the shared allocation gate."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.domain.clock import MatchPhase
from goaletto.domain.discipline import settle_cards
from goaletto.domain.match_codec import state_hash
from goaletto.domain.match_types import MatchState
from goaletto.infrastructure.models import (
    ChampionsTie,
    DisciplineReceipt,
    Fixture,
    FixturePlan,
    MatchDiscipline,
    Person,
    Player,
    PlayerDiscipline,
)


async def tournament_scope(session: AsyncSession, fixture: Fixture) -> str | None:
    if fixture.competition == "friendly":
        return None
    plan = await session.get(FixturePlan, fixture.id)
    if plan is None:
        return None  # Operator/offline fixtures do not create official bans.
    if (
        plan.announced_at is None
        or plan.season_id != fixture.season_id
        or plan.competition != (fixture.competition or "league")
        or plan.league_id != fixture.league_id
        or plan.round_number != fixture.round_number
        or (plan.home_club_id, plan.away_club_id) != (fixture.home_club_id, fixture.away_club_id)
    ):
        raise ValueError("Discipline needs an authenticated announced plan")
    if plan.competition == "league":
        return f"league:{fixture.season_id}:{fixture.league_id}"
    if plan.competition == "cup":
        return f"cup:{fixture.season_id}"
    if plan.competition == "champions":
        parts = (plan.conditional_ref or "").split(":")
        if len(parts) != 4 or parts[0] != "champions-tie" or parts[2] != "leg":
            raise ValueError("Unknown champions disciplinary scope")
        tie = await session.get(ChampionsTie, UUID(parts[1]))
        if tie is None or tie.season_id != fixture.season_id:
            raise ValueError("Unknown champions tournament")
        return f"champions:{tie.tournament_id}"
    raise ValueError("Unknown disciplinary competition")


async def balances(
    session: AsyncSession, scope: str | None, person_ids: list[UUID]
) -> dict[UUID, PlayerDiscipline]:
    if scope is None or not person_ids:
        return {}
    return {
        row.person_id: row
        for row in await session.scalars(
            select(PlayerDiscipline)
            .where(PlayerDiscipline.scope == scope, PlayerDiscipline.person_id.in_(person_ids))
            .execution_options(populate_existing=True)
        )
    }


async def exclusions(
    session: AsyncSession, scope: str | None, fixture: Fixture
) -> list[dict[str, str]]:
    if scope is None:
        return []
    rows = (
        await session.execute(
            select(PlayerDiscipline, Player, Person)
            .join(Player, Player.person_id == PlayerDiscipline.person_id)
            .join(Person, Person.id == Player.person_id)
            .where(
                PlayerDiscipline.scope == scope,
                PlayerDiscipline.bans > 0,
                Player.club_id.in_((fixture.home_club_id, fixture.away_club_id)),
                Player.status != "retired",
                Person.career == "player",
            )
            .order_by(Player.person_id)
        )
    ).all()
    return [
        {"person_id": str(p.person_id), "club_id": str(p.club_id), "name": person.name}
        for _, p, person in rows
    ]


async def apply_discipline(session: AsyncSession, state: MatchState, at: datetime) -> None:
    """One terminal receipt, including cards in abandoned games; no score invention.

    Only a completed game serves bans. Eligibility is frozen at lineup lock,
    so a transfer, release or ownership change cannot rewrite who sat it out.
    """
    if not session.in_transaction():
        raise ValueError("Discipline needs a caller-owned transaction")
    from goaletto.application.game_actions import allocation_lock

    await allocation_lock(session)
    header = await session.scalar(
        select(MatchDiscipline)
        .where(MatchDiscipline.match_id == state.match_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if header is None:
        return  # Already locked legacy game, friendly or operator fixture.
    if state.phase not in {MatchPhase.FULL_TIME, MatchPhase.ABANDONED}:
        raise ValueError("Discipline requires a terminal checkpoint")
    digest = state_hash(state)
    if header.applied_at is not None:
        if header.state_hash != digest:
            raise ValueError("Disciplinary checkpoint changed")
        return
    # person -> frozen club, name, yellows, dismissed, serving
    facts: dict[UUID, tuple[UUID, str, int, bool, bool]] = {
        UUID(r["person_id"]): (UUID(r["club_id"]), r["name"], 0, False, True)
        for r in header.excluded
    }
    for team in (state.home, state.away):
        names = {p.id: p.name for p in team.snapshot.players}
        for c in team.conditions:
            if c.player_id in facts:
                raise ValueError("Suspended player appeared in the match squad")
            if c.yellows or c.dismissed:
                facts[c.player_id] = (
                    team.snapshot.club_id,
                    names[c.player_id],
                    c.yellows,
                    c.dismissed,
                    False,
                )
    accounts = await balances(session, header.scope, list(facts))
    for person_id, (club_id, name, yellows, dismissed, serving) in facts.items():
        account = accounts.get(person_id)
        if account is None:
            account = PlayerDiscipline(scope=header.scope, person_id=person_id, yellows=0, bans=0)
            session.add(account)
        if account.bans and not serving:
            raise ValueError("Suspended player was admitted")
        change = settle_cards(
            account.yellows,
            account.bans,
            yellows=yellows,
            dismissed=dismissed,
            serving=serving,
            completed=state.phase == MatchPhase.FULL_TIME,
        )
        account.yellows, account.bans = change.yellows, change.bans
        session.add(
            DisciplineReceipt(
                match_id=state.match_id,
                person_id=person_id,
                club_id=club_id,
                name=name,
                yellows=yellows,
                dismissed=dismissed,
                served=change.served,
                awarded=change.awarded,
                carried_after=change.yellows,
                bans_after=change.bans,
            )
        )
    await session.flush()
    header.applied_at, header.state_hash = at, digest
    await session.flush()
