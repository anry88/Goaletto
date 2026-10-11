"""Assign complete, age-compatible illustrations without repeating people."""

import hashlib
from pathlib import Path
from uuid import UUID

from goaletto.application.persons import AppearanceExhausted
from goaletto.domain.world import WorldPlan
from goaletto.portraits.whole import WholePortraitRecipe, load_whole_catalog

PLAYER_BANDS = (
    ("player-youth", 17, 21),
    ("player-adult", 22, 27),
    ("player-mature", 28, 31),
    ("player-veteran", 32, 38),
)


def age_band(age: int) -> str:
    for name, low, high in PLAYER_BANDS:
        if low <= age <= high:
            return name
    raise ValueError("Unsupported footballer portrait age")


def assign_world_portraits(
    plan: WorldPlan,
    *,
    root: Path | None = None,
    reserved: frozenset[str] = frozenset(),
) -> dict[UUID, WholePortraitRecipe]:
    catalog = load_whole_catalog(root)
    candidates: dict[str, dict[str, str]] = {}
    for base_id in catalog["banks"]["players"]:
        base = catalog["variants"][base_id]
        identity = base["identity_id"]
        if identity in reserved:
            continue
        if identity in candidates:
            raise ValueError("Player bank repeats an identity")
        variants = {age_band(base["depicted_age"]): base_id}
        for variant_id in catalog["identities"][identity]["variants"]:
            entry = catalog["variants"][variant_id]
            if entry.get("stage") in {band[0] for band in PLAYER_BANDS}:
                if entry["bank"] != "players" or entry["identity_id"] != identity:
                    raise ValueError("Invalid player age variant")
                variants[entry["stage"]] = variant_id
        candidates[identity] = variants
    if len(candidates) < len(plan.players):
        raise AppearanceExhausted("World needs 1000 unused player identities")
    people: dict[str, list[UUID]] = {name: [] for name, _, _ in PLAYER_BANDS}
    for person in plan.players:
        people[age_band(person.age)].append(person.id)
    demand = {name: len(ids) for name, ids in people.items()}
    allocated: dict[str, list[str]] = {name: [] for name in people}

    def digest(key: str) -> bytes:
        return hashlib.sha256(f"{plan.options.seed}:{plan.options.key}:{key}".encode()).digest()

    def place(identity: str, visited_bands: set[str]) -> bool:
        # An augmenting path crosses at most four age bands, never 1000 recursive people.
        for band in sorted(candidates[identity], key=lambda name: digest(f"{identity}:{name}")):
            if band in visited_bands:
                continue
            visited_bands.add(band)
            if len(allocated[band]) < demand[band]:
                allocated[band].append(identity)
                return True
            for current in tuple(allocated[band]):
                if place(current, visited_bands):
                    allocated[band].remove(current)
                    allocated[band].append(identity)
                    return True
        return False

    identities = sorted(
        candidates, key=lambda identity: (len(candidates[identity]), digest(identity))
    )
    for identity in identities:
        if sum(map(len, allocated.values())) == len(plan.players):
            break
        place(identity, set())
    if any(len(allocated[band]) != count for band, count in demand.items()):
        raise AppearanceExhausted("Not enough age-compatible faces; publish the missing variants")
    result: dict[UUID, WholePortraitRecipe] = {}
    for band, ids in people.items():
        chosen = sorted(allocated[band], key=digest)
        for person_id, identity in zip(
            sorted(ids, key=lambda p: digest(str(p))), chosen, strict=True
        ):
            result[person_id] = WholePortraitRecipe(
                catalog["catalog_version"],
                identity,
                candidates[identity][band],
            )
    return result
