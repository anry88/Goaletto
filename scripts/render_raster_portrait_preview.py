"""Generate review portraits from actual local parts, with no network calls."""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from goaletto.portraits.raster import make_raster_recipe, render_raster

OUTPUT = Path("assets/portraits/raster-pilot/previews")
FONT = ImageFont.load_default(size=16)
INK = "#2A2824"


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    sheet = Image.new("RGB", (1536, 650), "#E8DECB")
    label = ImageDraw.Draw(sheet)
    label.text((20, 12), "GOALETTO / ACTUAL MODULAR PNG ASSEMBLY", fill=INK, font=FONT)
    recipes = []
    for row, bank in enumerate(("players", "staff")):
        for column in range(6):
            seed = f"modular-{bank}-{column + 1:02}"
            recipe = make_raster_recipe(seed, bank)
            age = (
                (19, 24, 27, 30, 34, 38)[column]
                if bank == "players"
                else (40, 44, 48, 53, 58, 63)[column]
            )
            portrait = render_raster(
                recipe,
                age=age,
                kit_primary="#743D42" if row == 0 else "#6B755D",
                kit_secondary="#F2E9D8",
                size=256,
            )
            portrait.save(OUTPUT / f"{seed}.png")
            (OUTPUT / f"{seed}.json").write_text(recipe.to_json() + "\n", encoding="utf-8")
            recipes.append(json.loads(recipe.to_json()))
            x, y = column * 256, 45 + row * 300
            sheet.paste(portrait.convert("RGB"), (x, y))
            label.text(
                (x + 12, y + 260), f"{bank.upper()} {column + 1:02} / {age}", fill=INK, font=FONT
            )
    sheet.save(OUTPUT / "generated-lineup-v01.png")
    (OUTPUT / "recipes-v01.json").write_text(json.dumps(recipes, indent=2) + "\n", encoding="utf-8")

    recipe = make_raster_recipe("modular-players-02", "players")
    variants = Image.new("RGB", (768, 900), "#E8DECB")
    label = ImageDraw.Draw(variants)
    for row, (primary, secondary) in enumerate(
        [("#743D42", "#F2E9D8"), ("#6B755D", "#F2E9D8"), ("#2B435C", "#DFA642")]
    ):
        for column, age in enumerate((20, 30, 35)):
            portrait = render_raster(
                recipe,
                age=age,
                kit_primary=primary,
                kit_secondary=secondary,
                size=256,
            )
            x, y = column * 256, row * 300
            variants.paste(portrait.convert("RGB"), (x, y))
            label.text((x + 12, y + 262), f"SAME RECIPE / AGE {age}", fill=INK, font=FONT)
    variants.save(OUTPUT / "identity-age-kit-v01.png")
    for bank, age in [("players", 24), ("staff", 55)]:
        render_raster(
            make_raster_recipe(f"modular-{bank}-02", bank),
            age=age,
            kit_primary="#743D42",
            kit_secondary="#F2E9D8",
            size=96,
        ).save(OUTPUT / f"{bank}-96px.png")
    print("12 assembled portraits, stable recipes, age/kit matrix and 96px checks saved.")


if __name__ == "__main__":
    main()
