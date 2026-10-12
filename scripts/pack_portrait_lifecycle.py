"""Import complete-person age/career atlases, retaining base IDs and sources."""

import argparse
import hashlib
import json
from pathlib import Path

from pack_portrait_atlases import clean_person, garment_mask, normalize_garment_edges
from PIL import Image, ImageOps

from goaletto.portraits.raster import tint_kit

ROOT = Path(__file__).resolve().parents[1] / "assets/portraits/whole-v2"
LIFE = ROOT / "lifecycle-v1"


def clean_lifecycle_person(sprite: Image.Image) -> Image.Image:
    """Trim a previous row's jersey edge before extracting the complete portrait."""
    alpha = sprite.getchannel("A")
    values = list(alpha.get_flattened_data())
    pixels = list(sprite.get_flattened_data())
    width, height = sprite.size
    counts = [
        sum(v >= 16 for v in values[y * width : (y + 1) * width]) for y in range(height // 8 + 1)
    ]
    pigment = 0
    cutoff = None
    for row in range(height // 8):
        pigment += sum(
            a >= 16 and (min(r, b) - g > 35 or min(g, b) - r > 35)
            for r, g, b, a in pixels[row * width : (row + 1) * width]
        )
        # Jersey pigments distinguish the broad intruding edge from actual hair.
        # A very thin black shoulder baseline can also intrude without pigment.
        # A sloping jersey hem can taper to half the tile width before the
        # transparent gutter; requiring 55% misses that final opaque row.
        if counts[row] >= width * 0.5 and counts[row + 1] < width * 0.5:
            if pigment >= width * 2 or row < 4:
                cutoff = row + 1
    if cutoff is not None:
        while cutoff < height // 8:
            edge = pixels[cutoff * width : (cutoff + 1) * width]
            opaque = sum(a >= 192 for _, _, _, a in edge)
            if counts[cutoff] > width * 0.2 and opaque < width * 0.05:
                cutoff += 1
            else:
                break
        values[: cutoff * width] = [0] * (cutoff * width)
        # The antialiased edge can overlap a few crown pixels. Remove only the
        # unmistakable jersey pigment there, retaining the actual hair contour.
        for index, (red, green, blue, opacity) in enumerate(pixels[: width * (height // 8)]):
            if opacity >= 16 and (min(red, blue) - green > 90 or min(green, blue) - red > 90):
                values[index] = 0
        alpha.putdata(values)
        sprite.putalpha(alpha)
    return clean_person(sprite)


def main(allow_partial: bool = False) -> None:
    plan = json.loads((LIFE / "production-plan.json").read_text())
    base = json.loads((ROOT / "manifest.json").read_text())
    folders = ("portraits", "masks", "thumbnails", "previews/portraits")
    for folder in folders:
        (LIFE / folder).mkdir(parents=True, exist_ok=True)
    missing = [
        a["name"] for a in plan["atlases"] if not (LIFE / "sources" / f"{a['name']}.png").exists()
    ]
    if missing and not allow_partial:
        raise ValueError(f"Lifecycle sources incomplete: {len(missing)} of {len(plan['atlases'])}")
    old_path = LIFE / "manifest.json"
    old = json.loads(old_path.read_text()) if old_path.exists() else {"variants": {}}
    extension = {
        "schema_version": 1,
        "base_catalog_version": base["catalog_version"],
        "status": "partial-generation" if missing else "illustrated-lifecycle-complete",
        "stages": [
            {key: value for key, value in stage.items() if key != "edit"}
            for stage in plan["stages"]
        ],
        "identities": {},
        "variants": {},
    }
    hashes: dict[str, str] = {}
    for atlas in plan["atlases"]:
        if atlas["name"] in missing:
            continue
        stage = next(s for s in plan["stages"] if s["id"] == atlas["stage"])
        with Image.open(LIFE / "sources" / f"{atlas['name']}.png") as source:
            if source.mode != "RGBA" or source.getchannel("A").getextrema()[0] != 0:
                raise ValueError(f"Source lacks real alpha: {atlas['name']}")
            width, height = source.size
            cols, rows = atlas["grid"]
            for cell in atlas["cells"]:
                row, col = divmod(cell["slot"], cols)
                crop = (
                    round(col * width / cols),
                    round(row * height / rows),
                    round((col + 1) * width / cols),
                    round((row + 1) * height / rows),
                )
                sprite = clean_lifecycle_person(source.crop(crop))
                bbox = sprite.getchannel("A").getbbox()
                if bbox is None or bbox[3] - bbox[1] < sprite.height // 2:
                    raise ValueError(f"Incomplete figure: {atlas['name']}/{cell['slot']}")
                sprite = ImageOps.contain(sprite.crop(bbox), (248, 248), Image.Resampling.LANCZOS)
                packed = Image.new("RGBA", (256, 256))
                packed.alpha_composite(sprite, ((256 - sprite.width) // 2, 256 - sprite.height))
                packed = normalize_garment_edges(packed)
                identity = f"dugout-whole-v2/{cell['identity']}"
                if identity not in base["identities"]:
                    raise ValueError(f"Foreign identity: {identity}")
                variant = f"{cell['identity']}-{stage['id']}-l1"
                digest = hashlib.sha256(packed.tobytes()).hexdigest()
                if digest in hashes:
                    raise ValueError(f"Duplicate pixels: {variant}/{hashes[digest]}")
                hashes[digest] = variant
                mask = garment_mask(packed)
                image_path, mask_path = f"portraits/{variant}.png", f"masks/{variant}.png"
                previous = old["variants"].get(variant, {})
                same = previous.get("pixel_sha256") == digest and all(
                    (LIFE / folder / f"{variant}{suffix}").exists()
                    for folder, suffix in (
                        ("portraits", ".png"),
                        ("masks", ".png"),
                        ("thumbnails", ".png"),
                        ("previews/portraits", ".webp"),
                    )
                )
                if not same:
                    packed.save(LIFE / image_path, optimize=True)
                    mask.save(LIFE / mask_path, optimize=True)
                    colored = tint_kit(packed, "#743D42", "#F2E9D8", material_mask=mask)
                    colored.save(
                        LIFE / "previews/portraits" / f"{variant}.webp", quality=90, method=4
                    )
                    colored.resize((96, 96), Image.Resampling.LANCZOS).save(
                        LIFE / "thumbnails" / f"{variant}.png", optimize=True
                    )
                extension["variants"][variant] = {
                    "identity_id": identity,
                    "bank": stage["bank"],
                    "stage": stage["id"],
                    "depicted_age": stage["depicted_age"],
                    "age_min": stage["age_min"],
                    "age_max": stage["age_max"],
                    "image": "lifecycle-v1/" + image_path,
                    "garment_mask": "lifecycle-v1/" + mask_path,
                    "source": f"lifecycle-v1/sources/{atlas['name']}.png",
                    "source_crop": list(crop),
                    "origin_source": atlas["base_source"],
                    "prompt": f"lifecycle-v1/prompts/{atlas['name']}.txt",
                    "pixel_sha256": digest,
                    "age_policy": (
                        "Prepared whole-person age band, same identity; game age is separate"
                    ),
                }
                extension["identities"].setdefault(identity, {})[stage["id"]] = variant
    if not missing:
        assert len(extension["variants"]) == plan["expected_variants"]
        assert len(extension["identities"]) == plan["expected_identities"]
        for identity, stages in extension["identities"].items():
            assert len(stages) == (7 if identity.split("/")[-1].startswith("p") else 3)
    # Readers see either the previous complete manifest or the new one, never half JSON.
    pending = LIFE / ".manifest.pending.json"
    pending.write_text(json.dumps(extension, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(LIFE / "manifest.json")
    report = {
        "identities": len(extension["identities"]),
        "variants": len(extension["variants"]),
        "target_variants": plan["expected_variants"],
        "missing_atlases": missing,
        "exact_duplicate_pixels": 0,
        "limits": (
            "Metadata/pixel uniqueness does not certify visual identity; inspect aging comparisons"
        ),
    }
    (LIFE / "import-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "missing_atlases"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-partial", action="store_true", help="Development pilot only")
    main(parser.parse_args().allow_partial)
