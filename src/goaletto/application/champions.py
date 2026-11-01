"""Persisted champions draws, brackets, progression and neutral venue receipts."""

from dataclasses import asdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid5

from sqlalchemy import Select, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.application.game_actions import allocation_lock
from goaletto.domain.champions_bracket import (
    CHAMPIONS_BRACKET_POLICY,
    ChampionsBracket,
    all_matches,
    build_champions_bracket,
    possible_clubs,
    resolve_slot,
    validate_against_group,
    validate_winners,
)
from goaletto.domain.champions_groups import (
    CHAMPIONS_GROUPS_POLICY,
    ChampionsGroupsResult,
    build_champions_groups,
)
from goaletto.domain.knockout import KNOCKOUT_POLICY, KnockoutTie, MatchResult
from goaletto.domain.knockout_match import KnockoutContext, decision
from goaletto.domain.league_allocation import LeagueSnapshot
from goaletto.domain.match_codec import restore_state, state_hash
from goaletto.domain.match_types import (
    EXPERIENCE_ENGINE_VERSION,
    KNOCKOUT_ENGINE_VERSION,
    POSITION_ENGINE_VERSION,
)
from goaletto.domain.stadium import Attendance
from goaletto.domain.standings import LeagueStanding as StandingRow
from goaletto.domain.world import stable_seed
from goaletto.infrastructure.models import (
    ChampionsDraw,
    ChampionsTournament,
    Club,
    Fixture,
    FixturePlan,
    League,
    Match,
    Season,
    SeasonClub,
    SeasonContestant,
)
from goaletto.infrastructure.models import (
    ChampionsTie as ChampionsTieModel,
)
from goaletto.infrastructure.models import MatchResult as ResultReceipt

if TYPE_CHECKING:
    from goaletto.application.allocation import ReadySeason


async def initialize_cohort_champions(
    session: AsyncSession,
    ready: list["ReadySeason"],
    future: dict[UUID, UUID],
    at: datetime,
) -> list[ChampionsTournament]:
    cohort = sorted(ready, key=lambda item: str(item.season.id))
    host = cohort[0]
    key = "champions:" + str(uuid5(host.season.id, ":".join(str(r.season.id) for r in cohort)))
    snapshots = tuple(
        LeagueSnapshot(
            cohort_key=key,
            season_id=item.season.id,
            league_id=league.id,
            league_set_id=item.season.league_set_id,
            tier=league.tier,
            order=tuple(row.club_id for row in item.tables[league.tier]),
        )
        for item in cohort
        for league in item.leagues
    )
    host_next = future[host.season.league_set_id]
    tournaments = await initialize_season_champions(
        session,
        host_next,
        host.season.id,
        host.tables,
        host.leagues,
        at,
        snapshots_override=snapshots,
    )
    draw = await session.scalar(select(ChampionsDraw).where(ChampionsDraw.season_id == host_next))
    assert draw is not None
    for item in cohort[1:]:
        upcoming = future[item.season.league_set_id]
        session.add(
            ChampionsDraw(
                id=uuid5(upcoming, f"champions-draw:{key}"),
                season_id=upcoming,
                qualifying_season_id=item.season.id,
                league_set_id=item.season.league_set_id,
                cohort_key=draw.cohort_key,
                seed=draw.seed,
                policy_version=draw.policy_version,
                snapshots=draw.snapshots,
                created_at=at,
            )
        )
    await session.flush()
    return tournaments


COMPETITION = "champions"


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


def _snapshot_to_json(snapshot: LeagueSnapshot) -> dict[str, Any]:
    return {
        "cohort_key": snapshot.cohort_key,
        "season_id": str(snapshot.season_id),
        "league_id": str(snapshot.league_id),
        "league_set_id": str(snapshot.league_set_id),
        "tier": snapshot.tier,
        "order": [str(club_id) for club_id in snapshot.order],
    }


def _snapshot_from_json(payload: dict[str, Any]) -> LeagueSnapshot:
    return LeagueSnapshot(
        cohort_key=payload["cohort_key"],
        season_id=UUID(payload["season_id"]),
        league_id=UUID(payload["league_id"]),
        league_set_id=UUID(payload["league_set_id"]),
        tier=payload["tier"],
        order=tuple(UUID(club_id) for club_id in payload["order"]),
    )


def _round_label(participant_count: int, round_number: int) -> str:
    total_rounds = participant_count.bit_length() - 1
    if round_number == total_rounds:
        return "final"
    if round_number == total_rounds - 1:
        return "semifinal"
    if round_number == total_rounds - 2:
        return "quarterfinal"
    return f"round_of_{2 ** (total_rounds - round_number + 1)}"


async def frozen_groups(session: AsyncSession, row: ChampionsDraw) -> ChampionsGroupsResult:
    snapshots = tuple(_snapshot_from_json(payload) for payload in row.snapshots)
    result = build_champions_groups(snapshots, seed=row.seed)
    if (
        result.policy_version != row.policy_version
        or result.cohort_key != row.cohort_key
        or result.seed != row.seed
    ):
        raise ValueError("Unknown persisted champions draw")
    return result


async def frozen_bracket(
    session: AsyncSession, tournament: ChampionsTournament
) -> ChampionsBracket:
    draw = await session.scalar(
        select(ChampionsDraw).where(ChampionsDraw.season_id == tournament.season_id)
    )
    if draw is None or draw.cohort_key != tournament.cohort_key:
        raise ValueError("Missing frozen champions draw")
    groups = await frozen_groups(session, draw)
    group = next(
        (
            item
            for item in groups.groups
            if item.tier == tournament.tier and item.group_index == tournament.group_index
        ),
        None,
    )
    if (
        group is None
        or tournament.participant_count != group.participant_count
        or tournament.seed != draw.seed
    ):
        raise ValueError("Tournament group left the frozen draw")
    bracket = build_champions_bracket(
        group=group, tournament_id=tournament.id, seed=tournament.bracket_seed
    )
    validate_against_group(bracket, group)
    if bracket.policy_version != tournament.policy_version:
        raise ValueError("Unknown persisted champions bracket policy")
    return bracket


async def initialize_season_champions(
    session: AsyncSession,
    next_season_id: UUID,
    qualifying_season_id: UUID,
    tables: dict[int, tuple[StandingRow, ...]],
    leagues: list[League],
    at: datetime,
    *,
    snapshots_override: tuple[LeagueSnapshot, ...] | None = None,
) -> list[ChampionsTournament]:
    """Persist qualification, brackets, ties and plans under one host season.

    Qualification comes from the frozen final tables of the completed
    qualifying season (top eight per league). The first season has no past
    table, so world initialization never calls this: no qualification means
    no champions cups, never invented clubs. Joint rollover supplies frozen
    snapshots across all compatible sets; guest eligibility does not change
    their actual destination league membership.
    """
    require_utc(at)
    await allocation_lock(session)
    existing = await session.scalar(
        select(ChampionsDraw).where(ChampionsDraw.season_id == next_season_id)
    )
    if existing is not None:
        await frozen_groups(session, existing)
        return list(
            (
                await session.scalars(
                    select(ChampionsTournament)
                    .where(ChampionsTournament.season_id == next_season_id)
                    .order_by(ChampionsTournament.tier, ChampionsTournament.group_index)
                )
            ).all()
        )
    season = await session.get(Season, next_season_id)
    if season is None or season.cycle_epoch is None:
        raise ValueError("Season cycle epoch required for champions planning")
    set_ids = {league.league_set_id for league in leagues}
    if len(set_ids) != 1:
        raise ValueError("Champions host needs exactly one league set")
    league_set_id = next(iter(set_ids))
    by_tier = {league.tier: league for league in leagues}
    if sorted(by_tier) != [1, 2, 3, 4, 5]:
        raise ValueError("Champions cohort needs tiers 1..5")
    cohort_key = f"champions:{qualifying_season_id.hex}"
    snapshots = tuple(
        LeagueSnapshot(
            cohort_key=cohort_key,
            season_id=qualifying_season_id,
            league_id=by_tier[tier].id,
            league_set_id=league_set_id,
            tier=tier,
            order=tuple(row.club_id for row in tables[tier]),
        )
        for tier in range(1, 6)
    )
    if snapshots_override is not None:
        snapshots = snapshots_override
        cohort_key = snapshots[0].cohort_key
    seed = stable_seed(season.seed, f"champions:{cohort_key}")
    groups = build_champions_groups(snapshots, seed=seed)
    draw = ChampionsDraw(
        id=uuid5(next_season_id, f"champions-draw:{cohort_key}"),
        season_id=next_season_id,
        qualifying_season_id=qualifying_season_id,
        league_set_id=league_set_id,
        cohort_key=cohort_key,
        seed=seed,
        policy_version=CHAMPIONS_GROUPS_POLICY,
        snapshots=[_snapshot_to_json(snapshot) for snapshot in snapshots],
        created_at=at,
    )
    session.add(draw)
    for snapshot in snapshots:
        for club_id in snapshot.order[:8]:
            await session.execute(
                insert(SeasonContestant)
                .values(
                    season_id=next_season_id,
                    club_id=club_id,
                )
                .on_conflict_do_nothing()
            )
    await session.flush()
    from goaletto.application.training import reserve_match_day

    tournaments: list[ChampionsTournament] = []
    for group in groups.groups:
        tournament_id = uuid5(
            next_season_id, f"champions:tier:{group.tier}:group:{group.group_index}"
        )
        bracket_seed = stable_seed(season.seed, f"champions-bracket:{tournament_id}")
        bracket = build_champions_bracket(
            group=group, tournament_id=tournament_id, seed=bracket_seed
        )
        validate_against_group(bracket, group)
        if bracket.policy_version != CHAMPIONS_BRACKET_POLICY:
            raise ValueError("Unknown champions bracket policy")
        tournament = ChampionsTournament(
            id=tournament_id,
            season_id=next_season_id,
            qualifying_season_id=qualifying_season_id,
            tier=group.tier,
            group_index=group.group_index,
            participant_count=group.participant_count,
            cohort_key=cohort_key,
            seed=seed,
            bracket_seed=bracket_seed,
            policy_version=bracket.policy_version,
            created_at=at,
        )
        session.add(tournament)
        tournaments.append(tournament)
        # Persist the parent receipt before its self-linked ties share one flush.
        await session.flush()
        for tie in bracket.ties:
            label = _round_label(group.participant_count, tie.round_number)
            session.add(
                ChampionsTieModel(
                    id=tie.id,
                    tournament_id=tournament_id,
                    season_id=next_season_id,
                    round_number=tie.round_number,
                    tie_index=tie.tie_index,
                    round_label=label,
                    home_kind=tie.home.kind,
                    home_club_id=tie.home.club_id,
                    home_tie_id=tie.home.tie_id,
                    away_kind=tie.away.kind,
                    away_club_id=tie.away.club_id,
                    away_tie_id=tie.away.tie_id,
                    champions_cycles=list(tie.champions_cycles),
                    neutral=tie.neutral,
                    status="pending",
                )
            )
        await session.flush()
        for leg in all_matches(bracket):
            plan_id = uuid5(next_season_id, f"champions:{leg.tie_id}:{leg.leg_number}")
            assigned_day = season.cycle_epoch + timedelta(days=4 * (leg.champions_cycle - 1) + 1)
            candidates: set[UUID] = set()
            if leg.home.kind == "club" and leg.home.club_id is not None:
                candidates.add(leg.home.club_id)
            else:
                candidates.update(possible_clubs(bracket, leg.home, {}))
            if leg.away.kind == "club" and leg.away.club_id is not None:
                candidates.add(leg.away.club_id)
            else:
                candidates.update(possible_clubs(bracket, leg.away, {}))
            session.add(
                FixturePlan(
                    id=plan_id,
                    season_id=next_season_id,
                    league_id=None,
                    competition=COMPETITION,
                    round_number=leg.champions_cycle,
                    stage=_round_label(group.participant_count, leg.round_number),
                    leg=leg.leg_number,
                    neutral=leg.neutral,
                    assigned_day=assigned_day,
                    home_club_id=leg.home.club_id if leg.home.kind == "club" else None,
                    away_club_id=leg.away.club_id if leg.away.kind == "club" else None,
                    conditional_ref=f"champions-tie:{leg.tie_id}:leg:{leg.leg_number}",
                    possible_club_ids=[str(c) for c in sorted(candidates, key=str)],
                )
            )
            await reserve_match_day(
                session, tuple(candidates), assigned_day, at, competition="champions"
            )
    await session.flush()
    return sorted(tournaments, key=lambda t: (t.tier, t.group_index))


async def context_for_champions_fixture(session: AsyncSession, fixture: Fixture) -> KnockoutContext:
    """Authenticate the exact frozen tie/leg; no relabelled operator admission."""
    plan = await session.get(FixturePlan, fixture.id)
    if (
        fixture.competition != COMPETITION
        or plan is None
        or plan.competition != COMPETITION
        or plan.conditional_ref is None
    ):
        raise ValueError("Champions fixture needs a genuine champions plan")
    parts = plan.conditional_ref.split(":")
    if len(parts) != 4 or parts[0] != "champions-tie" or parts[2] != "leg":
        raise ValueError("Malformed champions plan")
    tie_id = UUID(parts[1])
    leg = int(parts[3])
    tie = await session.get(ChampionsTieModel, tie_id, with_for_update=True)
    tournament = None
    if tie is not None:
        tournament = await session.get(ChampionsTournament, tie.tournament_id)
    if tie is None or tournament is None or tie.season_id != fixture.season_id:
        raise ValueError("Unknown champions tie")
    bracket = await frozen_bracket(session, tournament)
    definition = next((t for t in bracket.ties if t.id == tie.id), None)
    if (
        definition is None
        or tie.round_number != definition.round_number
        or tie.tie_index != definition.tie_index
        or tie.neutral != definition.neutral
        or tie.champions_cycles != list(definition.champions_cycles)
        or (tie.home_kind, tie.home_tie_id) != (definition.home.kind, definition.home.tie_id)
        or (tie.away_kind, tie.away_tie_id) != (definition.away.kind, definition.away.tie_id)
    ):
        raise ValueError("Champions tie differs from frozen draw")
    season_ties = (
        await session.scalars(
            select(ChampionsTieModel).where(ChampionsTieModel.tournament_id == tournament.id)
        )
    ).all()
    winners = {
        t.id: t.winner_club_id
        for t in season_ties
        if t.status == "decided" and t.winner_club_id is not None
    }
    validate_winners(bracket, winners)
    a, b = (
        resolve_slot(bracket, definition.home, winners),
        resolve_slot(bracket, definition.away, winners),
    )
    if a is None or b is None or (a, b) != (tie.home_club_id, tie.away_club_id):
        raise ValueError("Champions participants have unresolved predecessors")
    if tie.status == "decided":
        raise ValueError("Champions tie already decided")
    if leg not in ((1,) if tie.neutral else (1, 2)):
        raise ValueError("Invalid champions leg")
    expected = (b, a) if leg == 2 else (a, b)
    season = await session.get(Season, fixture.season_id)
    if season is None or season.cycle_epoch is None:
        raise ValueError("Champions season has no epoch")
    day = season.cycle_epoch + timedelta(days=4 * (tie.champions_cycles[leg - 1] - 1) + 1)
    recovered = (
        plan.revision > 1
        and plan.assigned_day > day
        and (plan.assigned_day - season.cycle_epoch).days % 4 == 1
    )
    label = _round_label(tournament.participant_count, tie.round_number)
    if (
        plan.id != uuid5(fixture.season_id, f"champions:{tie.id}:{leg}")
        or plan.season_id != fixture.season_id
        or plan.announced_at is None
        or (plan.assigned_day != day and not recovered)
        or plan.round_number != tie.champions_cycles[leg - 1]
        or fixture.round_number != plan.round_number
        or (plan.home_club_id, plan.away_club_id) != expected
        or (fixture.home_club_id, fixture.away_club_id) != expected
        or fixture.leg != leg
        or fixture.stage != label
        or fixture.neutral != tie.neutral
        or plan.stage != fixture.stage
        or plan.leg != fixture.leg
        or plan.neutral != fixture.neutral
    ):
        raise ValueError("Champions fixture does not match its structural plan")
    if leg == 2 and tie.first_match_result is None:
        from goaletto.application.match_runtime import NotReady

        raise NotReady("Return leg waits for the first result")
    return KnockoutContext(
        tie.id,
        KnockoutTie(KNOCKOUT_POLICY, a, b, "single" if tie.neutral else "two-leg"),
        "final" if tie.neutral else "leg2" if leg == 2 else "leg1",
        _load_segment(tie.first_match_result) if leg == 2 and tie.first_match_result else None,
    )


async def record_champions_match_result(
    session: AsyncSession, fixture: Fixture, match: Match, at: datetime
) -> ChampionsTieModel:
    require_utc(at)
    await allocation_lock(session)
    state = restore_state(match.state)
    k = state.knockout
    result = await session.get(ResultReceipt, match.id)
    if (
        k is None
        or state.engine_version
        not in {KNOCKOUT_ENGINE_VERSION, POSITION_ENGINE_VERSION, EXPERIENCE_ENGINE_VERSION}
        or result is None
        or result.state_hash != state_hash(state)
    ):
        raise ValueError("Champions progression needs a genuine finished knockout")
    target = await session.get(ChampionsTieModel, k.tie_id, with_for_update=True)
    if target is None or target.season_id != fixture.season_id:
        raise ValueError("Wrong champions tie result")
    if target.status == "decided":
        raise ValueError("Champions tie was already settled")
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
        await advance_champions_progression(
            session, fixture.season_id, target.id, outcome.winner, at
        )
        if fixture.neutral:
            from goaletto.application.cup import settle_final_revenue

            await settle_final_revenue(session, fixture, at, competition=COMPETITION)
    await session.flush()
    return target


async def advance_champions_progression(
    session: AsyncSession,
    season_id: UUID,
    tie_id: UUID,
    winner_id: UUID,
    at: datetime,
) -> None:
    """Populate the winner into downstream ties and fixture plans.

    Eliminated clubs leave ``FixturePlan.possible_club_ids`` of their own
    tournament; unknown opponents never acquire invented kickoff times.
    """
    tournament_id: UUID | None = await session.scalar(
        select(ChampionsTieModel.tournament_id).where(ChampionsTieModel.id == tie_id)
    )
    if tournament_id is None:
        raise ValueError("Missing champions tie")
    tournament = await session.get(ChampionsTournament, tournament_id)
    if tournament is None or tournament.season_id != season_id:
        raise ValueError("Missing frozen champions tournament")
    bracket = await frozen_bracket(session, tournament)
    season_ties = (
        await session.scalars(
            select(ChampionsTieModel)
            .where(ChampionsTieModel.tournament_id == tournament.id)
            .with_for_update()
        )
    ).all()
    winners = {
        t.id: t.winner_club_id
        for t in season_ties
        if t.status == "decided" and t.winner_club_id is not None
    }
    validate_winners(bracket, winners)
    if winners.get(tie_id) != winner_id:
        raise ValueError("Progression requires the persisted winner")
    for row in season_ties:
        if row.home_tie_id == tie_id:
            row.home_club_id = winner_id
        if row.away_tie_id == tie_id:
            row.away_club_id = winner_id
    decided = {t.id: t for t in season_ties if t.status == "decided" and t.winner_club_id}
    losers = {
        club_id
        for t in decided.values()
        for club_id in (t.home_club_id, t.away_club_id)
        if club_id is not None and club_id != t.winner_club_id
    }
    plans = (
        await session.scalars(
            select(FixturePlan)
            .where(FixturePlan.season_id == season_id, FixturePlan.competition == COMPETITION)
            .with_for_update()
        )
    ).all()
    tie_by_id = {t.id: t for t in season_ties}
    for plan in plans:
        if plan.conditional_ref is None:
            continue
        parts = plan.conditional_ref.split(":")
        if len(parts) != 4 or parts[0] != "champions-tie" or parts[2] != "leg":
            continue
        try:
            plan_tie_id = UUID(parts[1])
            plan_leg = int(parts[3])
        except (ValueError, AttributeError):
            continue
        downstream = tie_by_id.get(plan_tie_id)
        if downstream is None or downstream.tournament_id != tournament.id:
            continue
        home_id = downstream.home_club_id
        away_id = downstream.away_club_id
        if home_id is not None and away_id is not None:
            if downstream.neutral:
                plan.home_club_id = home_id
                plan.away_club_id = away_id
            elif plan_leg == 1:
                plan.home_club_id = home_id
                plan.away_club_id = away_id
            elif plan_leg == 2:
                plan.home_club_id = away_id
                plan.away_club_id = home_id
            plan.possible_club_ids = []
        else:
            plan.possible_club_ids = [
                club_id for club_id in plan.possible_club_ids if UUID(club_id) not in losers
            ]


async def pin_champions_venue(
    session: AsyncSession, fixture: Fixture, home: Club, away: Club, seed: int, at: datetime
) -> Attendance:
    """Book a real third club's free stadium for a champions final."""
    from goaletto.application.cup import pin_final_venue

    return await pin_final_venue(session, fixture, home, away, seed, at, competition=COMPETITION)


def host_seasons_for(season_id: UUID) -> Select[tuple[UUID]]:
    """Shared tournament hosts for one club's actual playing season."""
    cohort = select(ChampionsDraw.cohort_key).where(ChampionsDraw.season_id == season_id)
    return select(ChampionsTournament.season_id).where(ChampionsTournament.cohort_key.in_(cohort))


async def league_season_for(session: AsyncSession, host_season_id: UUID, club_id: UUID) -> UUID:
    """Resolve a champions guest's actual league calendar within its frozen cohort."""
    cohort = (
        select(ChampionsDraw.cohort_key)
        .where(ChampionsDraw.season_id == host_season_id)
        .scalar_subquery()
    )
    rows = await session.scalars(
        select(SeasonClub.season_id)
        .join(ChampionsDraw, ChampionsDraw.season_id == SeasonClub.season_id)
        .where(SeasonClub.club_id == club_id, ChampionsDraw.cohort_key == cohort)
    )
    season_id = rows.one_or_none()
    if season_id is None:
        raise ValueError("Champions participant has no cohort league membership")
    return season_id
