"""Illustrated PNG generator with saved identities and current club kit colors."""

import argparse
from pathlib import Path

from goaletto.portraits.raster import make_raster_recipe, recipe_from_json, render_raster


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a modular Dugout PNG portrait locally")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--seed")
    source.add_argument("--recipe", type=Path)
    parser.add_argument("--bank", choices=["players", "staff"])
    parser.add_argument("--age", type=int, required=True)
    parser.add_argument("--kit-primary", required=True)
    parser.add_argument("--kit-secondary", required=True)
    parser.add_argument("--size", type=int, choices=[96, 256, 512], default=512)
    parser.add_argument("--transparent", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--recipe-output", type=Path)
    args = parser.parse_args()
    if args.seed is not None:
        if args.bank is None:
            parser.error("--bank is required with --seed")
        recipe = make_raster_recipe(args.seed, args.bank)
    else:
        recipe = recipe_from_json(args.recipe.read_text(encoding="utf-8"))
        if args.bank is not None and args.bank != recipe.bank:
            parser.error("--bank must match the saved recipe")
    image = render_raster(
        recipe,
        age=args.age,
        kit_primary=args.kit_primary,
        kit_secondary=args.kit_secondary,
        size=args.size,
        background=None if args.transparent else "#E8DECB",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output, format="PNG")
    if args.recipe_output:
        args.recipe_output.parent.mkdir(parents=True, exist_ok=True)
        args.recipe_output.write_text(recipe.to_json() + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
