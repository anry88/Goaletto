"""Local illustrated-part compositor; no image generation or network at runtime."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from PIL import Image, ImageChops

from goaletto.portraits.compositor import asset_root

LAYERS = ("head", "torso", "age_details", "eyes", "nose", "mouth", "hair")
IDENTITY_LAYERS = ("head", "eyes", "nose", "mouth", "hair", "torso")
HEX = re.compile(r"#[0-9a-fA-F]{6}\Z")


@dataclass(frozen=True)
class RasterRecipe:
    asset_version: int
    bank: str
    seed: str
    components: dict[str, str]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, indent=2)


def raster_root() -> Path:
    return asset_root() / "raster-pilot"


def load_catalog(root: Path | None = None) -> dict[str, Any]:
    catalog: dict[str, Any] = json.loads(
        ((root or raster_root()) / "manifest-v1.json").read_text(encoding="utf-8")
    )
    return catalog


def make_raster_recipe(seed: str, bank: str, root: Path | None = None) -> RasterRecipe:
    catalog = load_catalog(root)
    if bank not in catalog["banks"]:
        raise ValueError("Portrait bank must be players or staff")
    components = {}
    for layer in IDENTITY_LAYERS:
        choices = catalog["banks"][bank][layer]
        digest = hashlib.sha256(f"goaletto:raster:1:{bank}:{seed}:{layer}".encode()).digest()
        components[layer] = choices[int.from_bytes(digest[:8], "big") % len(choices)]
    return RasterRecipe(catalog["asset_version"], bank, seed, components)


def recipe_from_json(value: str) -> RasterRecipe:
    data = json.loads(value)
    if not isinstance(data, dict) or set(data) != {"asset_version", "bank", "seed", "components"}:
        raise ValueError("Invalid raster recipe")
    if (
        type(data["asset_version"]) is not int
        or not isinstance(data["bank"], str)
        or not isinstance(data["seed"], str)
        or not isinstance(data["components"], dict)
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in data["components"].items())
    ):
        raise ValueError("Invalid raster recipe fields")
    return RasterRecipe(**data)


def age_stage(bank: str, age: int) -> str:
    if type(age) is not int or age < 17:
        raise ValueError("Age must be an integer of at least 17")
    if bank == "players":
        if age > 38:
            raise ValueError("Active footballer age cannot exceed 38")
        return "none" if age < 28 else "subtle" if age < 32 else "mature"
    if bank == "staff":
        return "none" if age < 45 else "subtle" if age < 55 else "mature"
    raise ValueError("Unknown portrait bank")


def _rgb(color: str) -> tuple[int, int, int]:
    if not HEX.fullmatch(color):
        raise ValueError("Only six-digit hex colors are allowed")
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def tint_kit(
    image: Image.Image,
    primary: str,
    secondary: str,
    *,
    material_mask: Image.Image | None = None,
) -> Image.Image:
    """Replace magenta/cyan material colors; retain ink, shading and alpha."""
    main, trim = _rgb(primary), _rgb(secondary)
    result = image.convert("RGBA")
    if material_mask is not None and (
        material_mask.mode != "L" or material_mask.size != result.size
    ):
        raise ValueError("Material mask must be a matching grayscale image")
    mask_pixels = material_mask.get_flattened_data() if material_mask is not None else None
    pixels: list[tuple[int, int, int, int]] = []
    for index, pixel in enumerate(result.get_flattened_data()):
        red, green, blue, alpha = cast(tuple[int, int, int, int], pixel)
        if alpha < 16 or (mask_pixels is not None and mask_pixels[index] == 0):
            # Near-transparent RGB noise from generated cutouts is not a material mask.
            pixels.append((red, green, blue, alpha))
            continue
        if min(red, blue) - green > 35:
            material, intensity = main, max(red, blue) / 255
        elif min(green, blue) - red > 35:
            material, intensity = trim, max(green, blue) / 255
        else:
            pixels.append((red, green, blue, alpha))
            continue
        pixels.append(
            (
                round(material[0] * intensity),
                round(material[1] * intensity),
                round(material[2] * intensity),
                alpha,
            )
        )
    result.putdata(pixels)
    return result


def _skin_overlay(image: Image.Image, tone: str) -> Image.Image:
    """Match small warm skin edges in eye/lip sprites to the selected head."""
    if tone == "light":
        return image
    ratios = {"tan": (0.82, 0.76, 0.68), "deep": (0.49, 0.43, 0.39)}[tone]
    result = image.copy()
    pixels: list[tuple[int, int, int, int]] = []
    for pixel in result.get_flattened_data():
        red, green, blue, alpha = cast(tuple[int, int, int, int], pixel)
        # Keep sclera, pupils, lip pigment and charcoal unchanged.
        if red > 105 and 0.58 < green / red < 0.88 and 0.48 < blue / max(green, 1) < 0.93:
            red, green, blue = [
                round(channel * ratio)
                for channel, ratio in zip((red, green, blue), ratios, strict=True)
            ]
        pixels.append((red, green, blue, alpha))
    result.putdata(pixels)
    return result


def render_raster(
    recipe: RasterRecipe,
    *,
    age: int,
    kit_primary: str,
    kit_secondary: str,
    size: int = 512,
    background: str | None = "#E8DECB",
    root: Path | None = None,
) -> Image.Image:
    root = (root or raster_root()).resolve()
    catalog = load_catalog(root)
    if recipe.asset_version != catalog["asset_version"] or recipe.bank not in catalog["banks"]:
        raise ValueError("Unsupported raster recipe version or bank")
    if set(recipe.components) != set(IDENTITY_LAYERS):
        raise ValueError("Recipe must contain the complete identity layer set")
    bank = catalog["banks"][recipe.bank]
    for layer, component_id in recipe.components.items():
        if component_id not in bank[layer]:
            raise ValueError(f"Unknown component in {layer}")
    if type(size) is not int or size not in (96, 256, 512):
        raise ValueError("Output size must be 96, 256 or 512")
    _rgb(kit_primary)
    _rgb(kit_secondary)
    canvas = Image.new("RGBA", (512, 512), (*_rgb(background), 255) if background else (0, 0, 0, 0))
    stage = age_stage(recipe.bank, age)
    tone = catalog["components"][recipe.components["head"]]["skin_tone"]
    head_mask = Image.new("L", (512, 512))
    for layer in LAYERS:
        if layer == "age_details":
            if stage == "none":
                continue
            component_id = bank[layer][stage]
        else:
            component_id = recipe.components[layer]
            if component_id not in bank[layer]:
                raise ValueError(f"Unknown component in {layer}")
        entry = catalog["components"][component_id]
        path = (root / entry["path"]).resolve()
        if not path.is_relative_to(root) or path.suffix != ".png":
            raise ValueError("Component path must be a local PNG part")
        with Image.open(path) as source:
            part = source.convert("RGBA")
        if part.size != (512, 512):
            raise ValueError("Components must use the common 512 pixel canvas")
        if layer == "head":
            head_mask = part.getchannel("A")
        elif layer == "torso":
            part = tint_kit(part, kit_primary, kit_secondary)
        elif layer in ("eyes", "mouth"):
            part = _skin_overlay(part, tone)
        elif layer == "age_details":
            strength = (0.18 if stage == "subtle" else 0.28) if recipe.bank == "players" else 0.4
            alpha_lookup = [round(alpha * strength) for alpha in range(256)]
            part.putalpha(ImageChops.multiply(part.getchannel("A").point(alpha_lookup), head_mask))
        canvas.alpha_composite(part)
    if size != 512:
        canvas = canvas.resize((size, size), Image.Resampling.LANCZOS)
    return canvas
