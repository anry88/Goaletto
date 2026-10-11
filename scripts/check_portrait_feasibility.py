"""Compare rejected r1 parts, proportional assembly, and an intact portrait master."""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from goaletto.portraits.raster import load_catalog, recipe_from_json, render_raster, tint_kit

ROOT = Path(__file__).resolve().parents[1]
PARTS = ROOT / "assets/portraits/raster-pilot"
OUTPUT = ROOT / "assets/portraits/feasibility-v2"
# Hand-checked experimental locations; r1 IDs/images are never overwritten.
RECTS = {
    "head": (126, 52, 260, 356),
    "torso": (48, 345, 416, 210),
    "eyes": (153, 179, 206, 70),
    "nose": (224, 244, 64, 70),
    "mouth": (201, 301, 110, 60),
    "hair": (99, 18, 314, 204),
}


def place_proportionally(part: Image.Image, rect: tuple[int, int, int, int]) -> Image.Image:
    """Uniform scaling plus centering; never resize each axis independently."""
    bounds = part.getchannel("A").point(lambda value: 255 if value > 12 else 0).getbbox()
    if bounds is None:
        raise ValueError("Empty part")
    source = part.crop(bounds)
    fitted = ImageOps.contain(source, rect[2:], Image.Resampling.LANCZOS)
    # Aspect ratio can differ only by rounding to whole output pixels.
    assert abs(fitted.width * source.height - fitted.height * source.width) <= max(source.size)
    canvas = Image.new("RGBA", (512, 512))
    destination = (
        rect[0] + (rect[2] - fitted.width) // 2,
        rect[1] + (rect[3] - fitted.height) // 2,
    )
    canvas.alpha_composite(fitted, destination)
    return canvas


def proportional_sample() -> Image.Image:
    catalog = load_catalog()
    recipe = recipe_from_json((PARTS / "previews/modular-players-02.json").read_text())
    result = Image.new("RGBA", (512, 512), "#E8DECB")
    for layer in ("head", "torso", "eyes", "nose", "mouth", "hair"):
        entry = catalog["components"][recipe.components[layer]]
        with Image.open(PARTS / entry["source"]) as atlas:
            part = atlas.convert("RGBA").crop(entry["crop"])
        if layer == "head":
            # Trim lower neck only. The skull/jaw are not geometrically squeezed.
            part = part.crop((0, 0, part.width, 355))
        if layer == "torso":
            part = tint_kit(part, "#743D42", "#F2E9D8")
        result.alpha_composite(place_proportionally(part, RECTS[layer]))
    return result


def whole_sample(
    primary: str, secondary: str, filename: str = "player-master-v02.png"
) -> Image.Image:
    with Image.open(OUTPUT / filename) as master:
        # This measured ROI belongs to this pilot master, not every future portrait.
        # In production each approved master needs its own semantic clothing masks.
        mask = Image.new("L", master.size)
        ImageDraw.Draw(mask).rectangle(
            (0, round(master.height * 0.55), master.width, master.height), fill=255
        )
        sprite = tint_kit(master.convert("RGBA"), primary, secondary, material_mask=mask)
    fitted = ImageOps.contain(sprite, (512, 512), Image.Resampling.LANCZOS)
    result = Image.new("RGBA", (512, 512), "#E8DECB")
    result.alpha_composite(fitted, ((512 - fitted.width) // 2, (512 - fitted.height) // 2))
    return result


def main() -> None:
    recipe = recipe_from_json((PARTS / "previews/modular-players-02.json").read_text())
    samples = [
        (
            "A / REJECTED R1",
            render_raster(recipe, age=24, kit_primary="#743D42", kit_secondary="#F2E9D8"),
        ),
        ("B / PROPORTIONAL PARTS", proportional_sample()),
        ("C / INTACT MASTER", whole_sample("#743D42", "#F2E9D8")),
    ]
    font = ImageFont.load_default(size=19)
    comparison = Image.new("RGB", (1536, 590), "#E8DECB")
    label = ImageDraw.Draw(comparison)
    for index, (title, image) in enumerate(samples):
        image.save(OUTPUT / f"comparison-{index + 1}.png")
        comparison.paste(image.convert("RGB"), (index * 512, 40))
        label.text((index * 512 + 16, 12), title, fill="#2A2824", font=font)
        thumbnail = image.resize((96, 96), Image.Resampling.LANCZOS)
        thumbnail.save(OUTPUT / f"comparison-{index + 1}-96px.png")
    comparison.save(OUTPUT / "comparison-v01.png")
    colors = [("#743D42", "#F2E9D8"), ("#6B755D", "#F2E9D8"), ("#2B435C", "#DFA642")]
    kit_sheet = Image.new("RGB", (1536, 552), "#E8DECB")
    for index, (primary, secondary) in enumerate(colors):
        image = whole_sample(primary, secondary)
        image.save(OUTPUT / f"whole-kit-{index + 1}.png")
        kit_sheet.paste(image.convert("RGB"), (index * 512, 0))
    kit_sheet.save(OUTPUT / "whole-kit-v01.png")
    young = whole_sample("#743D42", "#F2E9D8")
    mature = whole_sample("#743D42", "#F2E9D8", "player-age35-v01.png")
    age_sheet = Image.new("RGB", (1024, 552), "#E8DECB")
    age_sheet.paste(young.convert("RGB"), (0, 0))
    age_sheet.paste(mature.convert("RGB"), (512, 0))
    label = ImageDraw.Draw(age_sheet)
    label.text((16, 518), "SAME IDENTITY / 24", fill="#2A2824", font=font)
    label.text((528, 518), "SAME IDENTITY / 35 (OFFLINE VARIANT)", fill="#2A2824", font=font)
    age_sheet.save(OUTPUT / "whole-age-v01.png")
    # A real rendered-image invariant: recoloring must not touch the entire head.
    a, b = whole_sample(*colors[0]), whole_sample(*colors[2])
    face_boxes = [(0, 0, 512, 270), (210, 270, 302, 303)]
    for face_box in face_boxes:
        assert a.crop(face_box).tobytes() == b.crop(face_box).tobytes()
    assert a.crop((0, 350, 512, 512)).tobytes() != b.crop((0, 350, 512, 512)).tobytes()
    (OUTPUT / "experiment.json").write_text(
        json.dumps(
            {
                "date": "2026-10-11",
                "status": "visual-feasibility-experiment-awaiting-review",
                "cases": [title for title, _ in samples],
                "proportional_rects": RECTS,
                "limits": (
                    "One player identity, two offline age variants; "
                    "no large catalog or staff proof."
                ),
                "checks": {"face_pixels_unchanged_by_kit": True, "uniform_component_scaling": True},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
