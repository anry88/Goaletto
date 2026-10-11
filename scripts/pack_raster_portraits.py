"""Import purpose-generated transparent PART atlases, never slice finished portraits."""

import json
from pathlib import Path
from typing import Any

from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / "assets" / "portraits" / "raster-pilot"
SLOTS = (
    ("head", "oval", (138, 60, 236, 376)),
    ("head", "square", (124, 60, 264, 376)),
    ("head", "long", (144, 52, 224, 384)),
    ("hair", "swept", (100, 25, 312, 194)),
    ("hair", "curls", (102, 25, 308, 190)),
    ("hair", "crop", (110, 35, 292, 180)),
    ("eyes", "calm", (162, 181, 188, 58)),
    ("eyes", "focused", (164, 185, 184, 52)),
    ("nose", "straight", (232, 241, 48, 54)),
    ("nose", "wide", (226, 241, 60, 54)),
    ("mouth", "neutral", (216, 308, 80, 32)),
    ("mouth", "curved", (214, 308, 84, 32)),
    ("torso", "round", (54, 386, 404, 126)),
    ("torso", "vneck", (54, 386, 404, 126)),
    ("age_details", "subtle", (145, 124, 222, 188)),
    ("age_details", "mature", (142, 121, 228, 194)),
)
# Measured crop regions: generated atlases do not follow a mathematically exact grid.
# These explicit imports are versioned along with the raw source PNGs.
REGIONS = {
    "players": [
        (0, 0, 335, 430),
        (335, 0, 654, 430),
        (654, 0, 944, 430),
        (944, 0, 1266, 430),
        (0, 430, 335, 730),
        (335, 430, 654, 730),
        (654, 430, 944, 730),
        (944, 430, 1266, 730),
        (0, 730, 335, 965),
        (335, 730, 654, 965),
        (654, 730, 944, 965),
        (944, 730, 1266, 965),
        (0, 965, 332, 1200),
        (332, 965, 662, 1200),
        (662, 965, 944, 1200),
        (944, 965, 1266, 1200),
    ],
    "staff": [
        (0, 0, 384, 350),
        (384, 0, 768, 350),
        (768, 0, 1152, 350),
        (1152, 0, 1536, 350),
        (0, 350, 384, 590),
        (384, 350, 768, 590),
        (768, 350, 1152, 590),
        (1152, 350, 1536, 590),
        (0, 590, 384, 755),
        (384, 590, 768, 755),
        (768, 590, 1152, 755),
        (1152, 590, 1536, 755),
        (0, 755, 400, 1003),
        (400, 755, 790, 1003),
        (790, 755, 1152, 1003),
        (1152, 755, 1536, 1003),
    ],
}


def main() -> None:
    catalog: dict[str, Any] = {
        "asset_version": 1,
        "status": "rejected-visual-quality",
        "canvas": [512, 512],
        "scope": "male players and male staff; separate banks",
        "banks": {},
        "components": {},
    }
    for bank in ("players", "staff"):
        filename = "players-atlas-v02.png" if bank == "players" else "staff-atlas-v01.png"
        with Image.open(ROOT / filename) as image:
            if image.mode != "RGBA" or image.getchannel("A").getextrema()[0] != 0:
                raise ValueError("Source atlas must have real transparent RGBA pixels")
            atlas = image.copy()
        choices: dict[str, Any] = {layer: [] for layer, _, _ in SLOTS}
        choices["age_details"] = {}
        for index, ((layer, name, rect), crop) in enumerate(zip(SLOTS, REGIONS[bank], strict=True)):
            if bank == "staff":
                if layer == "hair":
                    name = {
                        "swept": "sidepart-grey",
                        "curls": "receding-grey",
                        "crop": "curls-grey",
                    }[name]
                elif layer == "torso":
                    name = {"round": "polo", "vneck": "track"}[name]
            part = atlas.crop(crop)
            # Ignore transparent-color ghosts when locating the actual sprite.
            bbox = part.getchannel("A").point(lambda alpha: 255 if alpha > 12 else 0).getbbox()
            if bbox is None:
                raise ValueError(f"Empty atlas tile: {bank}/{layer}/{name}")
            part = part.crop(bbox).resize(rect[2:], Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (512, 512))
            canvas.alpha_composite(part, dest=rect[:2])
            component_id = f"dugout-r1/{bank}/{layer}-{name}"
            path = Path("components-v1") / bank / f"{layer}-{name}.png"
            (ROOT / path).parent.mkdir(parents=True, exist_ok=True)
            canvas.save(ROOT / path)
            entry: dict[str, Any] = {
                "path": path.as_posix(),
                "source": filename,
                "crop": list(crop),
            }
            if layer == "head":
                entry["skin_tone"] = ("light", "tan", "deep")[index]
            catalog["components"][component_id] = entry
            if layer == "age_details":
                choices[layer][name] = component_id
            else:
                choices[layer].append(component_id)
        catalog["banks"][bank] = choices
    (ROOT / "manifest-v1.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("Imported 32 transparent parts into separate banks; original SVG IDs unchanged.")


if __name__ == "__main__":
    main()
