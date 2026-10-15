"""Freeze staff at lineup lock and grant appearance growth with the final result."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.development import growth_budget, save_budget
from goaletto.application.training_staff import qualifications, staff_roster
from goaletto.domain.match_development import POLICY, develop_appearance
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
    MatchDevelopment,
    Person,
    Player,
    PlayerDevelopment,
)


async def pin_development(
    session: AsyncSession, fixture: Fixture, state: MatchState, at: datetime
) -> None:
    plan = await session.get(FixturePlan, fixture.id)
    eligible = bool(
        plan
        and plan.competition == "league"
        and plan.announced_at is not None
        and plan.announced_at <= at
        and plan.season_id == fixture.season_id
        and plan.league_id == fixture.league_id
        and plan.round_number == fixture.round_number
        and plan.assigned_day == fixture.scheduled_start_at.date()
        and (plan.home_club_id, plan.away_club_id) == (fixture.home_club_id, fixture.away_club_id)
    )
    staff = {
        str(team.snapshot.club_id): await staff_roster(session, team.snapshot.club_id, at)
        for team in (state.home, state.away)
    }
    session.add(
        MatchDevelopment(
            match_id=state.match_id,
            policy_version=POLICY,
            eligible=eligible,
            staff_snapshot=staff,
            locked_at=at,
            results=[],
        )
    )


async def apply_development(session: AsyncSession, state: MatchState, at: datetime) -> None:
    receipt = await session.scalar(
        select(MatchDevelopment)
        .where(MatchDevelopment.match_id == state.match_id)
        .with_for_update()
    )
    if receipt is None or receipt.applied_at is not None:
        return  # Old matches without a pinned policy never receive retroactive rewards.
    if receipt.policy_version != POLICY:
        raise ValueError("Unknown match development policy")
    results = []
    if receipt.eligible:
        for team in (state.home, state.away):
            staff = qualifications(receipt.staff_snapshot[str(team.snapshot.club_id)])
            conditions = {c.player_id: c for c in team.conditions}
            for snapshot in sorted(team.snapshot.players, key=lambda p: p.id):
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
                trained = develop_appearance(
                    TrainingPlayer(
                        person.id,
                        snapshot.age,
                        snapshot.position,
                        values,
                        dev.caps,
                        dev.progress,
                        player.form,
                        player.experience,
                    ),
                    minutes,
                    staff,
                    growth_budget(dev, person.age),
                )
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
    receipt.results, receipt.applied_at = results, at
