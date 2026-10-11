"""Deterministic composition of trusted local SVG parts, independent of Imagegen."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from html import escape
from pathlib import Path
from typing import Any

LAYERS = (
    "torso",
    "neck",
    "ears",
    "face",
    "eyes",
    "nose",
    "mouth",
    "age_details",
    "hair",
    "accessories",
)
COLORS = {
    "ink": "#2A2824",
    "background": "#E8DECB",
    "accent": "#93886D",
    "skin": "#D99F76",
    "skin_shadow": "#B67853",
    "hair": "#2F302D",
    "kit": "#256B65",
}
SKINS = [
    ("#F0C19C", "#CE9673"),
    ("#D99F76", "#B67853"),
    ("#AB7251", "#82533F"),
    ("#754A37", "#533427"),
]
HEX = re.compile(r"#[0-9a-fA-F]{6}\Z")


@dataclass(frozen=True)
class PortraitRecipe:
    schema_version: int
    bank: str
    seed: str
    components: dict[str, str]
    palette: dict[str, str]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, indent=2)


def asset_root() -> Path:
    source_root = Path(__file__).resolve().parents[3] / "assets" / "portraits"
    if source_root.exists():
        return source_root
    return Path.cwd() / "assets" / "portraits"


def load_manifest(root: Path | None = None) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(((root or asset_root()) / "manifest.json").read_text())
    return value


def _index(seed: str, bank: str, key: str, count: int) -> int:
    digest = hashlib.sha256(f"goaletto:v1:{bank}:{seed}:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % count


def make_recipe(seed: str, bank: str, root: Path | None = None) -> PortraitRecipe:
    manifest = load_manifest(root)
    if bank not in manifest["banks"]:
        raise ValueError("Portrait bank must be players or staff")
    choices = manifest["banks"][bank]
    components = {
        layer: choices[layer][_index(seed, bank, layer, len(choices[layer]))] for layer in LAYERS
    }
    palette = dict(COLORS)
    palette["skin"], palette["skin_shadow"] = SKINS[_index(seed, bank, "skin", len(SKINS))]
    palette["kit"] = ("#6B755D", "#743D42", "#394F49")[_index(seed, bank, "kit", 3)]
    if bank == "staff":
        palette["hair"] = ("#2F302D", "#85857B", "#B3B4A8")[_index(seed, bank, "hair-color", 3)]
    else:
        palette["hair"] = ("#2F302D", "#5E392C", "#A87D40")[_index(seed, bank, "hair-color", 3)]
    return PortraitRecipe(int(manifest["schema_version"]), bank, seed, components, palette)


def render_svg(recipe: PortraitRecipe, root: Path | None = None) -> str:
    root = (root or asset_root()).resolve()
    manifest = load_manifest(root)
    if recipe.schema_version != manifest["schema_version"] or recipe.bank not in manifest["banks"]:
        raise ValueError("Unsupported portrait recipe version or bank")
    if set(recipe.components) != set(LAYERS) or set(recipe.palette) != set(COLORS):
        raise ValueError("Recipe must contain the complete layer and color set")
    if not all(HEX.fullmatch(color) for color in recipe.palette.values()):
        raise ValueError("Only six-digit hex colors are allowed")
    parts = []
    for layer in LAYERS:
        component = recipe.components[layer]
        if component not in manifest["banks"][recipe.bank][layer]:
            raise ValueError(f"Unknown component in {layer}")
        path = (root / component).resolve()
        if not path.is_relative_to(root) or path.suffix != ".svg":
            raise ValueError("Component path must be a local SVG part")
        content = path.read_text(encoding="utf-8").format_map(recipe.palette)
        parts.append(f'<g id="{layer}">{content}</g>')
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">'
        f"<title>{escape(recipe.bank)} portrait {escape(recipe.seed)}</title>"
        f"<metadata>{escape(recipe.to_json())}</metadata>"
        f'<rect width="256" height="256" rx="24" fill="{recipe.palette["background"]}"/>'
        '<path d="M16 210H240M16 224H240" stroke="#DFA642" stroke-width="1" opacity=".25"/>'
        '<g stroke-linecap="round" stroke-linejoin="round">' + "".join(parts) + "</g></svg>"
    )
