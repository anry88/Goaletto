"""Freeze staff at lineup lock and grant appearance growth with the final result."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.application.development import growth_budget, save_budget
from goaletto.application.training_staff import qualifications, staff_roster
from goaletto.domain.clock import MatchPhase
from goaletto.domain.match_codec import state_hash
from goaletto.domain.match_development import POLICY, AppearanceGrowth, develop_appearance
from goaletto.domain.match_practice import (
    CURRENT_POLICY,
    DEFAULT_FRIENDLY_EXPERIENCE_FACTOR,
    DEFAULT_FRIENDLY_SKILL_FACTOR,
    MatchPracticeResult,
    develop_match_practice,
)
from goaletto.domain.match_types import MatchState
from goaletto.domain.training import (
    POLICY_VERSION,
    PROGRESS_KEYS,
    SKILLS,
    TrainingPlayer,
    initial_caps,
)
from goaletto.infrastructure.models import (
    Fixture,
    FixturePlan,
    Match,
    MatchDevelopment,
    MatchResult,
    Person,
    Player,
    PlayerDevelopment,
)


async def pin_development(
    session: AsyncSession, fixture: Fixture, state: MatchState, at: datetime
) -> None:
    require_utc(at)
    plan = await session.get(FixturePlan, fixture.id)
    match = await session.get(Match, state.match_id)
    eligible = bool(
        plan
        and match
        and match.fixture_id == fixture.id
        and match.seed == state.seed
        and state.phase == MatchPhase.FIRST_HALF
        and state.tick == 0
        and (state.home.snapshot.club_id, state.away.snapshot.club_id)
        == (fixture.home_club_id, fixture.away_club_id)
        and plan.competition in ("league", "cup", "champions", "friendly")
        and plan.competition == (fixture.competition or "league")
        and (plan.competition != "cup" or state.knockout is not None)
        and (plan.competition != "champions" or state.knockout is not None)
        and plan.announced_at is not None
        and plan.announced_at <= at
        and plan.season_id == fixture.season_id
        and (plan.competition != "league" or plan.league_id == fixture.league_id)
        and plan.round_number == fixture.round_number
        and plan.assigned_day == fixture.scheduled_start_at.date()
        and (plan.home_club_id, plan.away_club_id) == (fixture.home_club_id, fixture.away_club_id)
    )
    staff = {
        str(team.snapshot.club_id): await staff_roster(session, team.snapshot.club_id, at)
        for team in (state.home, state.away)
    }
    if eligible and plan is not None and plan.competition == "cup":
        from goaletto.application.cup import context_for_fixture

        if await context_for_fixture(session, fixture) != state.knockout:
            raise ValueError("Practice context differs from the authenticated cup plan")
    if eligible and plan is not None and plan.competition == "champions":
        from goaletto.application.champions import context_for_champions_fixture

        if await context_for_champions_fixture(session, fixture) != state.knockout:
            raise ValueError("Practice context differs from the authenticated champions plan")
    session.add(
        MatchDevelopment(
            match_id=state.match_id,
            policy_version=CURRENT_POLICY,
            eligible=eligible,
            staff_snapshot=staff,
            terms_snapshot={
                "competition": plan.competition if plan else "league",
                "fixture_id": str(fixture.id),
                "season_id": str(fixture.season_id),
                "plan_revision": plan.revision if plan else None,
                "match_seed": f"{state.match_id}:{state.seed}",
                "friendly_skill_factor": DEFAULT_FRIENDLY_SKILL_FACTOR,
                "friendly_experience_factor": DEFAULT_FRIENDLY_EXPERIENCE_FACTOR,
                "players": {
                    str(p.id): {
                        "age": p.age,
                        "position": p.position,
                        "club_id": str(t.snapshot.club_id),
                    }
                    for t in (state.home, state.away)
                    for p in t.snapshot.players
                },
            },
            locked_at=at,
            results=[],
        )
    )


async def apply_development(session: AsyncSession, state: MatchState, at: datetime) -> None:
    require_utc(at)
    if not session.in_transaction():
        raise ValueError("Development needs the caller's transaction")
    from goaletto.application.game_actions import allocation_lock

    await allocation_lock(session)
    receipt = await session.scalar(
        select(MatchDevelopment)
        .where(MatchDevelopment.match_id == state.match_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if receipt is None or receipt.applied_at is not None:
        return  # Old matches without a pinned policy never receive retroactive rewards.
    if receipt.policy_version not in (POLICY, CURRENT_POLICY):
        raise ValueError("Unknown match development policy")
    practice = receipt.policy_version == CURRENT_POLICY
    results = []
    if receipt.eligible:
        if practice:
            final = await session.get(MatchResult, state.match_id)
            if (
                state.phase != MatchPhase.FULL_TIME
                or final is None
                or final.state_hash != state_hash(state)
                or at < receipt.locked_at
                or receipt.terms_snapshot["match_seed"] != f"{state.match_id}:{state.seed}"
                or receipt.terms_snapshot["fixture_id"] != str(final.fixture_id)
                or receipt.terms_snapshot["season_id"] != str(final.season_id)
                or receipt.terms_snapshot["competition"] != final.competition
            ):
                raise ValueError("Practice needs its finalized gameplay result")
        for team in (state.home, state.away):
            staff = qualifications(receipt.staff_snapshot[str(team.snapshot.club_id)])
            conditions = {c.player_id: c for c in team.conditions}
            for snapshot in sorted(team.snapshot.players, key=lambda p: p.id):
                if practice and receipt.terms_snapshot["players"].get(str(snapshot.id)) != {
                    "age": snapshot.age,
                    "position": snapshot.position,
                    "club_id": str(team.snapshot.club_id),
                }:
                    raise ValueError("Practice participant changed after lineup lock")
                minutes = conditions[snapshot.id].minutes_played
                if not minutes:
                    continue
                player = await session.scalar(
                    select(Player)
                    .where(Player.person_id == snapshot.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                person = await session.get(Person, snapshot.id)
                if (
                    player is None
                    or person is None
                    or player.club_id != team.snapshot.club_id
                    or person.age != snapshot.age
                    or person.career != "player"
                ):
                    raise ValueError("Appearance player changed before finalization")
                dev = await session.scalar(
                    select(PlayerDevelopment)
                    .where(PlayerDevelopment.person_id == person.id)
                    .with_for_update()
                )
                values = {key: getattr(player, key) for key in SKILLS}
                if dev is None:
                    dev = PlayerDevelopment(
                        person_id=person.id,
                        caps=initial_caps(person.id, values),
                        progress=dict.fromkeys(PROGRESS_KEYS, 0),
                        policy_version=POLICY_VERSION,
                    )
                    session.add(dev)
                if dev.policy_version != POLICY_VERSION:
                    raise ValueError("Unknown potential policy")
                before_form = player.form
                participant = TrainingPlayer(
                    person.id,
                    snapshot.age,
                    snapshot.position,
                    values,
                    dev.caps,
                    dev.progress,
                    player.form,
                    player.experience,
                )
                budget = growth_budget(dev, person.age)
                trained: AppearanceGrowth | MatchPracticeResult
                if practice:
                    practiced = develop_match_practice(
                        participant,
                        receipt.terms_snapshot["competition"],
                        minutes,
                        staff,
                        budget,
                        match_seed=receipt.terms_snapshot["match_seed"],
                        friendly_skill_factor=receipt.terms_snapshot["friendly_skill_factor"],
                        friendly_experience_factor=receipt.terms_snapshot[
                            "friendly_experience_factor"
                        ],
                        policy=receipt.policy_version,
                    )
                    player.form = practiced.form
                    trained = practiced
                else:
                    trained = develop_appearance(participant, minutes, staff, budget)
                growth = trained.growth
                before_experience = player.experience
                for key, value in growth.skills.items():
                    setattr(player, key, value)
                player.experience = trained.experience
                dev.progress = growth.progress
                save_budget(dev, growth.budget)
                results.append(
                    {
                        "person_id": str(person.id),
                        "club_id": str(player.club_id),
                        "minutes": minutes,
                        "position": snapshot.position,
                        "age": snapshot.age,
                        "gain_bps": sum(growth.granted.values()),
                        "granted": growth.granted,
                        "experience_before": before_experience,
                        "experience_after": player.experience,
                        "experience_gain": trained.experience_gain,
                        "season_gains": dict(growth.budget.used),
                        "changes": {
                            k: {"before": values[k], "after": growth.skills[k]}
                            for k in SKILLS
                            if values[k] != growth.skills[k]
                        },
                    }
                )
                if practice:
                    results[-1].update(
                        form_before=before_form,
                        form_after=player.form,
                        form_draw=practiced.form_draw,
                        form_up_chance=practiced.form_chance,
                        form_down_chance=practiced.form_down_chance,
                        competition=practiced.terms.competition,
                        skill_factor=practiced.terms.skill_factor,
                        experience_factor=practiced.terms.experience_factor,
                    )
    receipt.results, receipt.applied_at = results, at
