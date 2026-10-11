"""Review nine actual part assemblies; no generation of whole portraits or network."""

import json
from pathlib import Path

from check_portrait_feasibility import PARTS, RECTS, place_proportionally
from PIL import Image, ImageDraw, ImageFont

from goaletto.portraits.raster import RasterRecipe, _skin_overlay, load_catalog, tint_kit

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "assets/portraits/combinations-v01"
LAYERS = ("head", "torso", "eyes", "nose", "mouth", "hair")
PAPER = "#E8DECB"
INK = "#2A2824"


def source_part(component_id: str) -> Image.Image:
    entry = load_catalog()["components"][component_id]
    with Image.open(PARTS / entry["source"]) as atlas:
        return atlas.convert("RGBA").crop(entry["crop"])


def assemble(recipe: RasterRecipe) -> tuple[Image.Image, list[Image.Image]]:
    """Use the proportional B placement, with skin-edge adaptation for dark heads."""
    catalog = load_catalog()
    tone = catalog["components"][recipe.components["head"]]["skin_tone"]
    canvas = Image.new("RGBA", (512, 512), PAPER)
    stages = []
    for layer in LAYERS:
        part = source_part(recipe.components[layer])
        if layer == "head":
            part = part.crop((0, 0, part.width, 355))
        elif layer == "torso":
            part = tint_kit(part, "#743D42", "#F2E9D8")
        elif layer in ("eyes", "mouth"):
            part = _skin_overlay(part, tone)
        canvas.alpha_composite(place_proportionally(part, RECTS[layer]))
        stages.append(canvas.copy())
    return canvas, stages


def main() -> None:
    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / "recipes").mkdir(exist_ok=True)
    bank = load_catalog()["banks"]["players"]
    font = ImageFont.load_default(size=18)
    small_font = ImageFont.load_default(size=15)
    sheet = Image.new("RGB", (1536, 1752), PAPER)
    thumbnails = Image.new("RGB", (384, 384), PAPER)
    records = []
    for head in range(3):
        for hair in range(3):
            index = head * 3 + hair
            # Deliberate coverage rather than cherry-picking favorable seeded results.
            indices = {
                "head": head,
                "hair": hair,
                "eyes": (head + hair) % 2,
                "nose": (head + hair // 2) % 2,
                "mouth": hair % 2,
                "torso": (head + hair) % 2,
            }
            recipe = RasterRecipe(
                asset_version=1,
                bank="players",
                seed=f"combination-review-{index + 1:02}",
                components={layer: bank[layer][choice] for layer, choice in indices.items()},
            )
            image, stages = assemble(recipe)
            filename = f"player-{index + 1:02}.png"
            image.save(OUTPUT / filename)
            recipe_path = f"recipes/player-{index + 1:02}.json"
            (OUTPUT / recipe_path).write_text(recipe.to_json() + "\n", encoding="utf-8")
            col, row = hair * 512, head * 584
            sheet.paste(image.convert("RGB"), (col, row + 35))
            draw = ImageDraw.Draw(sheet)
            draw.text((col + 16, row + 8), f"{index + 1:02} / PART ASSEMBLY", fill=INK, font=font)
            parts = [recipe.components[layer].rsplit("/", 1)[1] for layer in LAYERS]
            draw.text((col + 12, row + 547), " / ".join(parts[:3]), fill=INK, font=small_font)
            draw.text((col + 12, row + 565), " / ".join(parts[3:]), fill=INK, font=small_font)
            thumbnail = image.resize((96, 96), Image.Resampling.LANCZOS)
            thumbnail.save(OUTPUT / f"player-{index + 1:02}-96px.png")
            thumbnails.paste(thumbnail.convert("RGB"), (hair * 128 + 16, head * 128 + 4))
            ImageDraw.Draw(thumbnails).text(
                (hair * 128 + 52, head * 128 + 102), f"{index + 1:02}", fill=INK, font=small_font
            )
            records.append({"image": filename, "recipe": recipe_path})
            if index == 1:
                breakdown = Image.new("RGB", (1536, 1104), PAPER)
                for stage_index, stage in enumerate(stages):
                    x, y = stage_index % 3 * 512, stage_index // 3 * 552
                    breakdown.paste(stage.convert("RGB"), (x, y + 40))
                    ImageDraw.Draw(breakdown).text(
                        (x + 16, y + 10),
                        f"{stage_index + 1} / + {LAYERS[stage_index].upper()}",
                        fill=INK,
                        font=font,
                    )
                breakdown.save(OUTPUT / "assembly-steps.png")
    sheet.save(OUTPUT / "combinations-sheet.png")
    thumbnails.save(OUTPUT / "telegram-96px.png")
    combinations = {
        tuple(sorted(json.loads((OUTPUT / record["recipe"]).read_text())["components"].items()))
        for record in records
    }
    assert len(combinations) == 9
    (OUTPUT / "review.json").write_text(
        json.dumps(
            {
                "status": "part-combination-review-awaiting-user-feedback",
                "source_catalog": "dugout-r1 (rejected original import preserved)",
                "placement": "feasibility B: proportional scaling; neck trim; skin-edge tint",
                "primary": "#743D42",
                "secondary": "#F2E9D8",
                "age_layers": False,
                "records": records,
                "limits": (
                    "Male players only; three source heads couple shape and skin tone. "
                    "No staff, aging or production compatibility guarantee."
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
