"""Provision one club for its new manager in the ownership transaction."""

from datetime import datetime
from random import Random
from uuid import UUID, uuid5

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.coaches import install_starting_coach
from goaletto.application.finances import close_salary, salary_contract
from goaletto.application.market import cancel_auction, prohibit_player_trades
from goaletto.application.money import locked_accounts, record, request
from goaletto.application.persons import NewPerson, create_person
from goaletto.application.player_release import offer_released_player
from goaletto.application.recovery import recover_player
from goaletto.application.staff_career import assign_career
from goaletto.application.staff_changes import release_staff
from goaletto.application.world_portraits import age_band
from goaletto.domain.finances import INITIAL_CAPITAL, STADIUM_CAPACITIES
from goaletto.domain.person_names import generated_name
from goaletto.domain.retirement import player_last_season
from goaletto.domain.starter_squads import starter_skills
from goaletto.domain.training import POLICY_VERSION, PROGRESS_KEYS, SKILLS, initial_caps
from goaletto.domain.world import ROSTER, SKILL_NAMES, stable_seed
from goaletto.infrastructure.models import (
    Auction,
    Club,
    ClubClaimReset,
    ClubLineup,
    LineupSlot,
    Person,
    PersonSeasonEvent,
    Player,
    PlayerDevelopment,
    Season,
    SeasonClub,
    StadiumExpansion,
    Staff,
    TrainingPlan,
)
from goaletto.portraits.whole import load_whole_catalog

STARTING_SUPPORTERS = 2500
STARTING_CAPACITY = STADIUM_CAPACITIES[0]
STARTING_ACADEMY = 1
POLICY = "club-claim-reset-v1"


async def reset_for_claim(
    session: AsyncSession, club: Club, ownership_id: UUID, at: datetime
) -> ClubClaimReset:
    """The current ownership is inserted only after old careers are closed."""
    season = await session.scalar(
        select(Season)
        .join(SeasonClub, SeasonClub.season_id == Season.id)
        .where(
            SeasonClub.club_id == club.id,
            Season.status.in_(("scheduled", "active", "finished")),
        )
        .order_by(Season.starts_at.desc(), Season.id)
        .limit(1)
    )
    if season is None:
        raise ValueError("Club has no season for its starting roster")
    account = (await locked_accounts(session, club.id))[club.id]
    if account.record.reserved or account.record.arrears:
        raise ValueError("Unowned club has outstanding reservations or legacy arrears")
    reset = ClubClaimReset(
        ownership_id=ownership_id,
        club_id=club.id,
        season_id=season.id,
        claimed_at=at,
        supporters_before=club.supporters,
        supporters_after=STARTING_SUPPORTERS,
        capacity_before=club.stadium_capacity,
        capacity_after=STARTING_CAPACITY,
        academy_level_before=club.academy_level,
        academy_level_after=STARTING_ACADEMY,
    )
    await _replace_roster(session, club, season, ownership_id, at)
    employees = (
        await session.execute(select(Staff, Person).join(Person).where(Staff.club_id == club.id))
    ).all()
    for worker, person in employees:
        person.home_league_set_id = season.league_set_id
        await release_staff(session, worker.person_id, club.id, at, normalize=False)
    await install_starting_coach(
        session,
        club.id,
        season,
        uuid5(ownership_id, "head-coach"),
        f"{POLICY}:{ownership_id}:head-coach",
        at,
    )
    await session.execute(
        update(TrainingPlan)
        .where(TrainingPlan.club_id == club.id, TrainingPlan.status == "scheduled")
        .values(status="cancelled")
    )
    await session.execute(
        update(StadiumExpansion)
        .where(StadiumExpansion.club_id == club.id, StadiumExpansion.status == "queued")
        .values(status="cancelled", cancelled_at=at)
    )
    club.supporters = STARTING_SUPPORTERS
    club.stadium_capacity = STARTING_CAPACITY
    club.academy_level = STARTING_ACADEMY
    delta = INITIAL_CAPITAL - club.budget
    if delta:
        await record(
            session,
            account,
            f"claim-balance:{ownership_id}",
            request(
                "claim_balance",
                abs(delta),
                {"policy": POLICY, "ownership_id": str(ownership_id), "target": INITIAL_CAPITAL},
            ),
            at,
            cash=delta,
        )
    return reset


async def _replace_roster(
    session: AsyncSession, club: Club, season: Season, ownership_id: UUID, at: datetime
) -> None:
    lineup = await session.get(ClubLineup, club.id)
    if lineup is None:
        raise ValueError("Club is missing its saved lineup")
    await session.execute(delete(LineupSlot).where(LineupSlot.club_id == club.id))
    players = (
        await session.execute(
            select(Player, Person).join(Person).where(Player.club_id == club.id).order_by(Person.id)
        )
    ).all()
    for player, person in players:
        condition = await recover_player(session, person.id, at)
        if condition.active_match_id is not None:
            raise ValueError("Player still belongs to an unfinished match")
        await close_salary(session, person.id, at)
        person.home_league_set_id = season.league_set_id
        if player.status == "active":
            auction = await session.scalar(
                select(Auction).where(Auction.person_id == person.id, Auction.status == "open")
            )
            if auction is not None:
                await cancel_auction(session, auction, "ownership_changed", at)
            await offer_released_player(session, player, person, at)
        elif player.status == "last_season" and player.last_season_id is not None:
            player.club_id = None
            player.status = "retired"
            player.retired_after_season_id = player.last_season_id
            person.career = "retired"
            session.add(
                PersonSeasonEvent(
                    id=uuid5(person.id, f"{POLICY}:{ownership_id}:retired"),
                    person_id=person.id,
                    season_id=player.last_season_id,
                    stage="player_retired",
                    age=person.age,
                    policy_version=POLICY,
                    occurred_at=at,
                )
            )
            await session.flush()
            await prohibit_player_trades(session, person.id, at)
            await assign_career(session, person, player.last_season_id, club.id, at)
        else:
            raise ValueError("Club roster contains an invalid player")
    lineup.formation = "4-4-2"
    lineup.tactics = {"mentality": "balanced", "pressing": False, "counterattack": False}
    lineup.version += 1
    lineup.tactics_version += 1
    catalog = load_whole_catalog()
    for slot, (position, age) in enumerate(ROSTER, 1):
        person_id = uuid5(ownership_id, f"player:{slot}")
        band = age_band(age)
        eligible = tuple(
            key
            for key, entry in catalog["variants"].items()
            if entry["bank"] == "players"
            and (
                entry.get("stage") == band
                or (
                    entry.get("depicted_age") is not None
                    and age_band(entry["depicted_age"]) == band
                )
            )
        )
        person = await create_person(
            session,
            NewPerson(
                id=person_id,
                creation_key=f"{POLICY}:{ownership_id}:player:{slot}",
                name=generated_name(person_id).name,
                age=age,
                career="player",
                origin_bank="players",
                eligible_variant_ids=eligible,
                home_league_set_id=season.league_set_id,
            ),
        )
        person.age_applied_for_season_id = season.id
        rng = Random(stable_seed(season.seed, str(person_id)))
        skills = starter_skills(position, rng)
        decision = player_last_season(age, rng.randrange(10_000))
        session.add(
            Player(
                person_id=person_id,
                club_id=club.id,
                position=position,
                **dict(zip(SKILL_NAMES, skills, strict=True)),
                form=6,
                form_activity_at=at,
                morale=50,
                status="last_season" if decision.is_last_season else "active",
                last_season_id=season.id if decision.is_last_season else None,
            )
        )
        session.add(
            PlayerDevelopment(
                person_id=person_id,
                caps=initial_caps(person_id, dict(zip(SKILLS, skills[:8], strict=True))),
                progress=dict.fromkeys(PROGRESS_KEYS, 0),
                policy_version=POLICY_VERSION,
            )
        )
        session.add_all(
            [
                PersonSeasonEvent(
                    id=uuid5(person_id, f"{POLICY}:{stage}"),
                    person_id=person_id,
                    season_id=season.id,
                    stage=stage,
                    age=age,
                    policy_version=decision.policy_version
                    if stage.endswith("decision")
                    else POLICY,
                    probability_bps=decision.probability_bps
                    if stage.endswith("decision")
                    else None,
                    draw=decision.draw if stage.endswith("decision") else None,
                    is_last_season=decision.is_last_season if stage.endswith("decision") else None,
                    occurred_at=at,
                )
                for stage in ("age_applied", "player_last_decision")
            ]
        )
        await session.flush()
        session.add(LineupSlot(club_id=club.id, slot=slot, person_id=person_id, position=position))
        await salary_contract(session, person_id, club.id, "player", at)
