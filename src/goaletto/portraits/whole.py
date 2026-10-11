"""Local whole-portrait selection and garment recoloring, independent of gameplay."""

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from goaletto.portraits.compositor import asset_root
from goaletto.portraits.raster import tint_kit


@dataclass(frozen=True)
class WholePortraitRecipe:
    catalog_version: int
    identity_id: str
    variant_id: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, indent=2)


def whole_root() -> Path:
    return asset_root() / "whole-v2"


def load_whole_catalog(root: Path | None = None) -> dict[str, Any]:
    catalog: dict[str, Any] = json.loads(
        ((root or whole_root()) / "manifest.json").read_text(encoding="utf-8")
    )
    extension_path = (root or whole_root()) / "lifecycle-v1/manifest.json"
    if extension_path.exists():
        extension = json.loads(extension_path.read_text(encoding="utf-8"))
        if extension["base_catalog_version"] != catalog["catalog_version"]:
            raise ValueError("Lifecycle extension targets a different catalog")
        for variant_id, entry in extension["variants"].items():
            identity = entry["identity_id"]
            if identity not in catalog["identities"] or variant_id in catalog["variants"]:
                raise ValueError("Lifecycle extension has foreign/duplicate identity or variant")
            catalog["variants"][variant_id] = entry
            catalog["identities"][identity]["variants"].append(variant_id)
        catalog["lifecycle"] = extension["identities"]
        catalog["age_stages"] = extension["stages"]
    return catalog


def recipe_for_variant(variant_id: str, root: Path | None = None) -> WholePortraitRecipe:
    catalog = load_whole_catalog(root)
    if variant_id not in catalog["variants"]:
        raise ValueError("Unknown whole-portrait variant")
    entry = catalog["variants"][variant_id]
    return WholePortraitRecipe(catalog["catalog_version"], entry["identity_id"], variant_id)


def make_whole_recipe(seed: str, bank: str, root: Path | None = None) -> WholePortraitRecipe:
    """Pick a preview only; this hash selection is NOT unique person allocation."""
    catalog = load_whole_catalog(root)
    if bank not in catalog["banks"]:
        raise ValueError("Unknown portrait bank")
    choices = catalog["banks"][bank]
    if not choices:
        raise ValueError("Empty portrait bank")
    digest = hashlib.sha256(f"goaletto:whole:1:{bank}:{seed}".encode()).digest()
    return recipe_for_variant(choices[int.from_bytes(digest[:8], "big") % len(choices)], root)


def plan_identity_allocation(
    person_ids: list[str],
    bank: str,
    seed: str,
    *,
    reserved: set[str] | None = None,
    root: Path | None = None,
) -> dict[str, WholePortraitRecipe]:
    """Plan without replacement; application must lock and persist reservations in PG."""
    if len(set(person_ids)) != len(person_ids):
        raise ValueError("Person IDs must be unique")
    catalog = load_whole_catalog(root)
    if bank not in catalog["banks"]:
        raise ValueError("Unknown portrait bank")
    used = reserved or set()
    choices = [
        WholePortraitRecipe(
            catalog["catalog_version"], catalog["variants"][variant]["identity_id"], variant
        )
        for variant in catalog["banks"][bank]
    ]
    choices = [recipe for recipe in choices if recipe.identity_id not in used]
    if len({recipe.identity_id for recipe in choices}) != len(choices):
        raise ValueError("Bank contains duplicate identities")
    if len(choices) < len(person_ids):
        raise ValueError("Portrait catalog exhausted; generate more unique identities")
    choices.sort(
        key=lambda recipe: hashlib.sha256(f"{seed}:{recipe.identity_id}".encode()).digest()
    )
    return dict(zip(sorted(person_ids), choices[: len(person_ids)], strict=True))


def career_variant(
    recipe: WholePortraitRecipe, variant_id: str, root: Path | None = None
) -> WholePortraitRecipe:
    """A career/age variant must belong to the same identity; never use a random face."""
    catalog = load_whole_catalog(root)
    if recipe.catalog_version != catalog["catalog_version"]:
        raise ValueError("Unsupported portrait catalog")
    current = recipe_for_variant(recipe.variant_id, root)
    target = recipe_for_variant(variant_id, root)
    if current != recipe or target.identity_id != recipe.identity_id:
        raise ValueError("Variant would change person identity")
    return target


def recipe_for_age(
    recipe: WholePortraitRecipe,
    *,
    bank: str,
    age: int,
    root: Path | None = None,
) -> WholePortraitRecipe:
    """Select an illustrated age/career band of this person, never another identity."""
    catalog = load_whole_catalog(root)
    if bank not in ("players", "staff") or type(age) is not int:
        raise ValueError("Age selection needs a valid bank and integer age")
    if recipe.catalog_version != catalog["catalog_version"]:
        raise ValueError("Unsupported portrait catalog")
    entry = catalog["variants"].get(recipe.variant_id)
    if entry is None or entry["identity_id"] != recipe.identity_id:
        raise ValueError("Recipe identity does not match variant")
    life = catalog.get("lifecycle", {}).get(recipe.identity_id, {})
    matches = [
        stage
        for stage in catalog.get("age_stages", [])
        if stage["bank"] == bank and stage["age_min"] <= age <= stage["age_max"]
    ]
    if len(matches) != 1 or matches[0]["id"] not in life:
        raise ValueError("Missing illustrated age/career band; no random face fallback")
    variant_id = life[matches[0]["id"]]
    target = catalog["variants"].get(variant_id)
    if target is None or target["identity_id"] != recipe.identity_id or target["bank"] != bank:
        raise ValueError("Invalid illustrated lifecycle variant")
    return WholePortraitRecipe(recipe.catalog_version, recipe.identity_id, variant_id)


def _local_png(root: Path, path: str) -> Path:
    result = (root / path).resolve()
    if not result.is_relative_to(root) or result.suffix != ".png":
        raise ValueError("Portrait assets must be local PNGs")
    return result


def render_whole(
    recipe: WholePortraitRecipe,
    *,
    kit_primary: str,
    kit_secondary: str,
    size: int = 256,
    root: Path | None = None,
) -> Image.Image:
    """Render a pinned illustration, without pretending to generate missing age stages."""
    root = (root or whole_root()).resolve()
    catalog = load_whole_catalog(root)
    if recipe.catalog_version != catalog["catalog_version"]:
        raise ValueError("Unsupported portrait catalog")
    if recipe_for_variant(recipe.variant_id, root) != recipe:
        raise ValueError("Recipe identity does not match variant")
    if type(size) is not int or size not in (96, 256):
        raise ValueError("Whole portraits export at96 or256 pixels")
    entry = catalog["variants"][recipe.variant_id]
    with Image.open(_local_png(root, entry["image"])) as source:
        image = source.convert("RGBA")
    with Image.open(_local_png(root, entry["garment_mask"])) as mask:
        if mask.mode != "L" or mask.size != image.size or image.size != (256, 256):
            raise ValueError("Invalid packed portrait or garment mask")
        result = tint_kit(image, kit_primary, kit_secondary, material_mask=mask)
    if size == 96:
        result = result.resize((96, 96), Image.Resampling.LANCZOS)
    return result
