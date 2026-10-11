"""Render one fixed whole-portrait variant in current club colors."""

import argparse
from pathlib import Path

from goaletto.portraits.compositor import asset_root
from goaletto.portraits.whole import recipe_for_age, recipe_for_variant, render_whole


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--catalog", type=int, choices=(1, 2), default=2)
    parser.add_argument("--age", type=int)
    parser.add_argument("--bank", choices=("players", "staff"))
    parser.add_argument("--primary", default="#743D42")
    parser.add_argument("--secondary", default="#F2E9D8")
    parser.add_argument("--size", type=int, choices=(96, 256), default=256)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = asset_root() / f"whole-v{args.catalog}"
    recipe = recipe_for_variant(args.variant, root)
    if (args.age is None) != (args.bank is None):
        parser.error("--age and --bank must be supplied together")
    if args.age is not None:
        recipe = recipe_for_age(recipe, bank=args.bank, age=args.age, root=root)
    image = render_whole(
        recipe, kit_primary=args.primary, kit_secondary=args.secondary, size=args.size, root=root
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output)


if __name__ == "__main__":
    main()
