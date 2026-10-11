import argparse
from pathlib import Path

from goaletto.portraits.compositor import make_recipe, render_svg


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a pilot Goaletto portrait as SVG")
    parser.add_argument("--seed", required=True)
    parser.add_argument("--bank", choices=["players", "staff"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--recipe-output", type=Path)
    args = parser.parse_args()
    recipe = make_recipe(args.seed, args.bank)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_svg(recipe), encoding="utf-8")
    if args.recipe_output:
        args.recipe_output.parent.mkdir(parents=True, exist_ok=True)
        args.recipe_output.write_text(recipe.to_json() + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
