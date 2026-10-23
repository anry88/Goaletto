"""Explicit, audited data migration of proven free starters. No world resets."""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from random import Random
from statistics import median
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import String, cast, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from goaletto.application.analytics import require_utc
from goaletto.application.club_valuation import valuation
from goaletto.application.game_actions import allocation_lock
from goaletto.application.recovery import recover_player
from goaletto.domain.finances import PRIMARY, player_fee
from goaletto.domain.starter_squads import (
    LEGACY_STARTER_POLICY,
    STARTER_POLICY,
    rebase_skills,
    starter_skills,
)
from goaletto.domain.world import SKILL_NAMES, stable_seed
from goaletto.infrastructure.models import (
    Auction,
    CandidateSigning,
    Club,
    FinanceAccount,
    Fixture,
    Match,
    Person,
    Player,
    PlayerRelease,
    StarterRebalanceReceipt,
    TransferReceipt,
    WorldSeedReceipt,
)

# Frozen original 20-slot identity contract, independent of future squad layouts.
_POSITIONS = (
    "GK",
    "DL",
    "DC",
    "DC",
    "DR",
    "ML",
    "MC",
    "MC",
    "MR",
    "FC",
    "FC",
    "GK",
    "DL",
    "DC",
    "DR",
    "MC",
    "ML",
    "MR",
    "FC",
    "FC",
)
_KEYS = SKILL_NAMES[:8]


@dataclass(frozen=True)
class Target:
    player: Player
    person: Person
    world_key: str
    before: tuple[int, ...]
    after: tuple[int, ...]
    old: tuple[int, ...]
    new: tuple[int, ...]


async def plan(session: AsyncSession, *, lock: bool = False) -> tuple[list[Target], dict[str, Any]]:
    source = (
        select(Player, Person, WorldSeedReceipt)
        .join(Person, Person.id == Player.person_id)
        .join(
            WorldSeedReceipt,
            Person.creation_key
            == ("world:" + WorldSeedReceipt.key + ":person:" + cast(Person.id, String)),
        )
        .where(Player.status != "retired", Person.career == "player")
        .order_by(Player.person_id)
        .execution_options(populate_existing=True)
    )
    rows = (
        await session.execute(source.with_for_update(of=(Player, Person)) if lock else source)
    ).all()
    source_ids = source.with_only_columns(Player.person_id).order_by(None)
    acquired = set(
        await session.scalars(
            select(TransferReceipt.person_id).where(TransferReceipt.person_id.in_(source_ids))
        )
    )
    acquired.update(
        await session.scalars(
            select(CandidateSigning.person_id).where(
                CandidateSigning.person_id.in_(source_ids), CandidateSigning.kind == "player"
            )
        )
    )
    acquired.update(
        await session.scalars(
            select(PlayerRelease.person_id).where(
                PlayerRelease.person_id.in_(source_ids), PlayerRelease.confirmed_at.is_not(None)
            )
        )
    )
    offered = set(
        await session.scalars(
            select(Auction.person_id).where(
                Auction.person_id.in_(source_ids), Auction.status == "open"
            )
        )
    )
    applied = set(await session.scalars(select(StarterRebalanceReceipt.person_id)))
    origins: dict[UUID, dict[UUID, tuple[UUID, str]]] = {}
    skipped: Counter[str] = Counter()
    targets = []
    for p, person, world in rows:
        if world.config.get("starter_policy", LEGACY_STARTER_POLICY) != LEGACY_STARTER_POLICY:
            skipped["new_policy"] += 1
            continue
        if p.person_id in applied:
            skipped["already_applied"] += 1
            continue
        if p.person_id in acquired:
            skipped["acquired_or_released"] += 1
            continue
        if p.person_id in offered:
            skipped["open_auction"] += 1
            continue
        if world.league_set_id not in origins:
            origins[world.league_set_id] = {
                uuid5(club, f"person:{slot}"): (club, position)
                for n in range(1, 51)
                for club in (uuid5(world.league_set_id, f"club:{n}"),)
                for slot, position in enumerate(_POSITIONS, 1)
            }
        if origins[world.league_set_id].get(p.person_id) != (p.club_id, p.position):
            skipped["origin_mismatch"] += 1
            continue
        seed = stable_seed(int(world.config["seed"]), str(p.person_id))
        old = starter_skills(p.position, Random(seed), LEGACY_STARTER_POLICY)[:8]
        new = starter_skills(p.position, Random(seed), STARTER_POLICY)[:8]
        before = tuple(getattr(p, key) for key in _KEYS)
        try:
            after = rebase_skills(before, old, new)
        except ValueError:
            skipped["nonstandard_skills"] += 1
            continue
        targets.append(Target(p, person, world.key, before, after, old, new))
    club_ids = {t.player.club_id for t in targets}
    live = list(
        await session.scalars(
            select(Fixture.id)
            .join(Match, Match.fixture_id == Fixture.id)
            .where(
                Fixture.status.in_(("scheduled", "live", "paused")),
                Fixture.home_club_id.in_(club_ids) | Fixture.away_club_id.in_(club_ids),
            )
        )
    )
    clubs = list(await session.scalars(select(Club).where(Club.id.in_(club_ids))))
    at_risk = 0
    for club in clubs:
        parts = await valuation(session, club)
        decrease = sum(
            player_fee(
                t.before, t.person.age, t.player.position, 1 + len(t.player.alternative_positions)
            )
            - player_fee(
                t.after, t.person.age, t.player.position, 1 + len(t.player.alternative_positions)
            )
            for t in targets
            if t.player.club_id == club.id
        )
        if max(0, -club.budget) > sum(parts.values()) - decrease:
            at_risk += 1
    identity = [(str(t.player.person_id), t.before, t.after) for t in targets]
    fingerprint = hashlib.sha256(json.dumps([STARTER_POLICY, identity]).encode()).hexdigest()

    def quality(values: tuple[int, ...], position: str) -> float:
        return (3 * values[PRIMARY[position]] + values[6] + values[7]) / 5

    return targets, {
        "policy": STARTER_POLICY,
        "eligible": len(targets),
        "clubs": len(clubs),
        "skipped": dict(skipped),
        "fingerprint": fingerprint,
        "locked_matches": len(live),
        "clubs_at_risk": at_risk,
        "quality_before_median": median(quality(t.before, t.player.position) for t in targets)
        if targets
        else None,
        "quality_after_median": median(quality(t.after, t.player.position) for t in targets)
        if targets
        else None,
    }


async def apply(
    session: AsyncSession, *, at: datetime, expected_fingerprint: str
) -> dict[str, Any]:
    require_utc(at)
    if not session.in_transaction():
        raise ValueError("Starter rebalancing needs a caller-owned transaction")
    if await session.scalar(text("SELECT current_database()")) != "goaletto":
        raise ValueError("Only the Goaletto application database may be migrated")
    await allocation_lock(session)
    list(await session.scalars(select(Club).order_by(Club.id).with_for_update()))
    accounts = list(
        await session.scalars(
            select(FinanceAccount)
            .order_by(FinanceAccount.club_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    targets, summary = await plan(session, lock=True)
    if summary["fingerprint"] != expected_fingerprint:
        raise ValueError("Migration preview changed; obtain a fresh preview")
    if summary["locked_matches"] or summary["clubs_at_risk"]:
        raise ValueError("Migration needs no locked matches and no insolvency caused by the change")
    for t in targets:
        condition = await recover_player(session, t.player.person_id, at)
        if condition.active_match_id is not None or condition.recovered_at > at:
            raise ValueError("Player recovery has an unfinished/future match")
        for key, value in zip(_KEYS, t.after, strict=True):
            setattr(t.player, key, value)
        condition.stamina = t.player.stamina
        session.add(
            StarterRebalanceReceipt(
                person_id=t.player.person_id,
                world_key=t.world_key,
                club_id=t.player.club_id,
                policy_version=STARTER_POLICY,
                before_skills=dict(zip(_KEYS, t.before, strict=True)),
                after_skills=dict(zip(_KEYS, t.after, strict=True)),
                old_baseline=dict(zip(_KEYS, t.old, strict=True)),
                new_baseline=dict(zip(_KEYS, t.new, strict=True)),
                applied_at=at,
            )
        )
    await session.flush()
    affected = {t.player.club_id for t in targets}
    for account in accounts:
        if account.club_id in affected:
            club = await session.get(Club, account.club_id)
            assert club is not None
            account.valuation = await valuation(session, club)
            account.credit_limit = sum(account.valuation.values())
    await session.flush()
    return {**summary, "applied": len(targets)}
