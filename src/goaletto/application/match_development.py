"""Freeze staff at lineup lock and grant appearance growth with the final result."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.application.development import growth_budget, position_experience, save_budget
from goaletto.application.training_staff import qualifications, staff_roster
from goaletto.domain.clock import MatchPhase
from goaletto.domain.match_codec import state_hash
from goaletto.domain.match_development import POLICY, AppearanceGrowth, develop_appearance
from goaletto.domain.match_performance import player_of_match
from goaletto.domain.match_practice import (
    CURRENT_POLICY,
    DEFAULT_FRIENDLY_EXPERIENCE_FACTOR,
    DEFAULT_FRIENDLY_SKILL_FACTOR,
    LEGACY_FRIENDLY_FACTOR,
    V2_POLICY,
    V3_POLICY,
    MatchPracticeResult,
    develop_match_practice,
    develop_position_practice,
)
from goaletto.domain.match_types import POSITION_ENGINE_VERSION, MatchState
from goaletto.domain.player_form import MatchPerformance
from goaletto.domain.position_experience import credit_experience
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
    if eligible and plan is not None and plan.competition == "friendly":
        from goaletto.application.friendly import authenticate_fixture

        await authenticate_fixture(session, fixture)
    session.add(
        MatchDevelopment(
            match_id=state.match_id,
            policy_version=CURRENT_POLICY
            if state.engine_version == POSITION_ENGINE_VERSION
            else V3_POLICY,
            eligible=eligible,
            staff_snapshot=staff,
            terms_snapshot={
                "injury_policy": "match-injury-v1",
                "injury_days": 7,
                "competition": plan.competition if plan else "league",
                "fixture_id": str(fixture.id),
                "season_id": str(fixture.season_id),
                "plan_revision": plan.revision if plan else None,
                "match_seed": f"{state.match_id}:{state.seed}",
                "friendly_skill_factor": DEFAULT_FRIENDLY_SKILL_FACTOR
                if state.engine_version == POSITION_ENGINE_VERSION
                else LEGACY_FRIENDLY_FACTOR,
                "friendly_experience_factor": DEFAULT_FRIENDLY_EXPERIENCE_FACTOR
                if state.engine_version == POSITION_ENGINE_VERSION
                else LEGACY_FRIENDLY_FACTOR,
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
    if receipt.policy_version not in (POLICY, V2_POLICY, V3_POLICY, CURRENT_POLICY):
        raise ValueError("Unknown match development policy")
    practice = receipt.policy_version in (V2_POLICY, V3_POLICY, CURRENT_POLICY)
    results = []
    best_player = player_of_match(state)
    if receipt.eligible:
        activity_at = at
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
            activity_at = final.actual_finish_at
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
                old_experience = position_experience(player, dev)
                old_progress = dict(dev.progress)
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
                    if receipt.policy_version == CURRENT_POLICY:
                        if state.engine_version != POSITION_ENGINE_VERSION:
                            raise ValueError("Positional development needs a V5 match")
                        exposure = dict(conditions[snapshot.id].position_minutes)
                        if sum(exposure.values()) != minutes:
                            raise ValueError("Positional minutes do not reconcile")
                        practiced = develop_position_practice(
                            participant,
                            receipt.terms_snapshot["competition"],
                            exposure,
                            staff,
                            budget,
                            match_seed=receipt.terms_snapshot["match_seed"],
                            friendly_skill_factor=receipt.terms_snapshot["friendly_skill_factor"],
                            friendly_experience_factor=receipt.terms_snapshot[
                                "friendly_experience_factor"
                            ],
                            performance=MatchPerformance(
                                minutes,
                                conditions[snapshot.id].energy_bps,
                                conditions[snapshot.id].goals,
                                conditions[snapshot.id].assists,
                                conditions[snapshot.id].saves,
                                conditions[snapshot.id].yellows,
                                conditions[snapshot.id].dismissed,
                                snapshot.id == best_player,
                            ),
                        )
                    else:
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
                experience_grants = (
                    practiced.experience_by_position
                    if receipt.policy_version == CURRENT_POLICY
                    else {snapshot.position: trained.experience_gain}
                )
                player.position_experience = credit_experience(old_experience, experience_grants)
                from goaletto.application.forms import form_activity

                form_activity(player, activity_at)
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
                if receipt.policy_version == CURRENT_POLICY:
                    results[-1].update(
                        goals=conditions[snapshot.id].goals,
                        assists=conditions[snapshot.id].assists,
                        saves=conditions[snapshot.id].saves,
                        player_of_match=snapshot.id == best_player,
                        position_terms=list(practiced.position_terms),
                        position_experience={
                            position: {
                                "before": old_experience.get(position, 0),
                                "after": player.position_experience[position],
                                "gain": gain,
                            }
                            for position, gain in experience_grants.items()
                        },
                        skill_progress={
                            skill: {
                                "before_value": values[skill],
                                "after_value": growth.skills[skill],
                                "before": old_progress[skill],
                                "after": growth.progress[skill],
                            }
                            for skill, gain in growth.granted.items()
                            if gain
                        },
                        growth_limited=sum(
                            int(term["requested_gain"]) for term in practiced.position_terms
                        )
                        > sum(growth.granted.values()),
                    )
    receipt.results, receipt.applied_at = results, at
