"""Export whole illustrations to256px assets and96px review sheets, retaining sources."""

import argparse
import json
from pathlib import Path
from typing import cast

from PIL import Image, ImageDraw, ImageFont, ImageOps

from goaletto.portraits.whole import recipe_for_variant, render_whole

ROOT = Path(__file__).resolve().parents[1] / "assets/portraits/whole-v1"
PLAYER_AGES = [
    24,
    23,
    28,
    20,
    26,
    21,
    31,
    19,
    27,
    22,
    30,
    18,
    25,
    29,
    20,
    33,
    24,
    28,
    21,
    26,
    32,
    23,
    27,
    19,
]
STAFF_AGES = [46, 58, 39, 63, 51, 44, 35, 66, 54, 41, 60, 48]


def garment_mask(image: Image.Image) -> Image.Image:
    """Explicit mask of technical garment pigments below the face; inspect each source."""
    pixels = []
    for index, pixel in enumerate(image.get_flattened_data()):
        red, green, blue, alpha = cast(tuple[int, int, int, int], pixel)
        material = min(red, blue) - green > 35 or min(green, blue) - red > 35
        below_face = index // image.width >= image.height // 2
        pixels.append(255 if alpha >= 16 and material and below_face else 0)
    mask = Image.new("L", image.size)
    mask.putdata(pixels)
    return mask


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    for folder in ("portraits", "masks", "previews", "thumbnails"):
        (ROOT / folder).mkdir(exist_ok=True)
    specs = []
    for bank, prefix, ages in (("players", "p", PLAYER_AGES), ("staff", "s", STAFF_AGES)):
        for index, age in enumerate(ages, 1):
            code = f"{prefix}{index:02}"
            specs.append((code, code, bank, age))
    specs.append(("p01-staff42", "p01", "staff", 42))
    catalog = {
        "catalog_version": 1,
        "status": "first-wave-illustrations-awaiting-review",
        "sizes": [96, 256],
        "banks": {"players": [], "staff": []},
        "variants": {},
        "identities": {},
    }
    for filename, identity, bank, age in specs:
        source_path = ROOT / "sources" / f"{filename}.png"
        if not source_path.exists():
            continue
        variant = f"{identity}-{bank[:-1] if bank == 'players' else 'staff'}{age}"
        identity_id = f"dugout-whole-v1/{identity}"
        with Image.open(source_path) as source:
            if source.mode != "RGBA" or source.getchannel("A").getextrema()[0] != 0:
                raise ValueError(f"Source requires actual alpha: {filename}")
            sprite = ImageOps.contain(source, (256, 256), Image.Resampling.LANCZOS)
        packed = Image.new("RGBA", (256, 256))
        packed.alpha_composite(sprite, ((256 - sprite.width) // 2, (256 - sprite.height) // 2))
        mask = garment_mask(packed)
        if mask.getbbox() is None:
            raise ValueError(f"No garment material pigments: {filename}")
        image_path, mask_path = f"portraits/{variant}.png", f"masks/{variant}.png"
        packed.save(ROOT / image_path, optimize=True)
        mask.save(ROOT / mask_path, optimize=True)
        catalog["variants"][variant] = {
            "identity_id": identity_id,
            "bank": bank,
            "depicted_age": age,
            "image": image_path,
            "garment_mask": mask_path,
            "source": f"sources/{filename}.png",
            "prompt": f"prompts/{filename}.txt",
            "review_status": "awaiting-user-review",
            "age_policy": "single illustrated age, no automatic age-stage completeness",
        }
        catalog["identities"].setdefault(identity_id, {"variants": []})["variants"].append(variant)
        # A linked career sample does not add a37th unrelated identity to the staff pool.
        if filename == identity:
            catalog["banks"][bank].append(variant)
    if args.require_complete:
        assert len(catalog["variants"]) == 37
        assert len(catalog["banks"]["players"]) == 24
        assert len(catalog["banks"]["staff"]) == 12
    (ROOT / "manifest.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
    font = ImageFont.load_default(size=15)
    for bank in ("players", "staff"):
        variants = catalog["banks"][bank]
        if not variants:
            continue
        sheet = Image.new("RGB", (6 * 192, ((len(variants) + 5) // 6) * 220), "#E8DECB")
        thumbs = Image.new("RGB", (6 * 112, ((len(variants) + 5) // 6) * 120), "#E8DECB")
        for index, variant in enumerate(variants):
            recipe = recipe_for_variant(variant, ROOT)
            portrait = render_whole(
                recipe, kit_primary="#743D42", kit_secondary="#F2E9D8", root=ROOT
            )
            x, y = index % 6 * 192, index // 6 * 220
            small = portrait.resize((192, 192), Image.Resampling.LANCZOS)
            sheet.paste(small, (x, y), small)
            ImageDraw.Draw(sheet).text((x + 10, y + 195), variant, font=font, fill="#2A2824")
            thumbnail = render_whole(
                recipe, kit_primary="#743D42", kit_secondary="#F2E9D8", size=96, root=ROOT
            )
            thumbnail.save(ROOT / "thumbnails" / f"{variant}.png", optimize=True)
            tx, ty = index % 6 * 112 + 8, index // 6 * 120
            thumbs.paste(thumbnail, (tx, ty), thumbnail)
            ImageDraw.Draw(thumbs).text((tx + 24, ty + 98), f"{index + 1:02}", fill="#2A2824")
        sheet.save(ROOT / "previews" / f"{bank}.png", optimize=True)
        thumbs.save(ROOT / "previews" / f"{bank}-96px.png", optimize=True)
    if "p01-staff42" in catalog["variants"]:
        pair = Image.new("RGB", (512, 286), "#E8DECB")
        for index, variant in enumerate(("p01-player24", "p01-staff42")):
            portrait = render_whole(
                recipe_for_variant(variant, ROOT),
                kit_primary="#743D42",
                kit_secondary="#F2E9D8",
                root=ROOT,
            )
            pair.paste(portrait, (index * 256, 0), portrait)
            ImageDraw.Draw(pair).text((index * 256 + 12, 263), variant, font=font, fill="#2A2824")
        pair.save(ROOT / "previews" / "player-to-staff.png", optimize=True)
    sizes = [path.stat().st_size for path in (ROOT / "portraits").glob("*.png")]
    print(
        json.dumps(
            {
                "variants": len(sizes),
                "total_png_bytes": sum(sizes),
                "mean_png_bytes": round(sum(sizes) / len(sizes)) if sizes else 0,
            }
        )
    )


if __name__ == "__main__":
    main()
