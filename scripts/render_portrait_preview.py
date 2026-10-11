"""Render original SVG parts to an approval gallery. No AI image editing."""

import json
from pathlib import Path
from typing import Any

from goaletto.portraits.compositor import make_recipe, render_svg


def main() -> None:
    output = Path("assets/portraits/previews")
    output.mkdir(parents=True, exist_ok=True)
    cards: list[str] = []
    svg_cards: list[str] = []
    recipes: list[dict[str, Any]] = []
    for index, (bank, seed) in enumerate(
        [
            ("players", "player-07"),
            ("players", "player-19"),
            ("players", "player-31"),
            ("staff", "staff-03"),
            ("staff", "staff-14"),
            ("staff", "staff-28"),
        ]
    ):
        recipe = make_recipe(seed, bank)
        portrait = render_svg(recipe)
        filename = f"{seed}.svg"
        (output / filename).write_text(portrait, encoding="utf-8")
        (output / f"{seed}.json").write_text(recipe.to_json() + "\n", encoding="utf-8")
        recipes.append(json.loads(recipe.to_json()))
        title = ("PLAYER" if bank == "players" else "STAFF") + f" / {index % 3 + 1:02}"
        cards.append(
            f'<figure><img src="{filename}" alt="{title}"><figcaption>{title}</figcaption>'
            f"<small>{seed}</small></figure>"
        )
        x, y = 64 + (index % 3) * 358, 176 + (index // 3) * 354
        body = portrait[portrait.index(">") + 1 : portrait.rindex("</svg>")]
        svg_cards.append(
            f'<g transform="translate({x} {y})">'
            '<rect width="318" height="320" rx="22" fill="#FFF9ED"/>'
            f'<svg x="31" y="12" width="256" height="256" viewBox="0 0 256 256">{body}</svg>'
            f'<text x="31" y="294" font-family="sans-serif" font-size="15" '
            f'letter-spacing="2" fill="#182B28">{title}</text></g>'
        )
    (output / "recipes.json").write_text(json.dumps(recipes, indent=2) + "\n")
    poster = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="960" viewBox="0 0 1200 960">'
        '<rect width="1200" height="960" fill="#E7DDC8"/>'
        '<text x="64" y="80" font-family="sans-serif" font-size="54" font-weight="bold" '
        'fill="#182B28">GOALETTO / ATELIER</text>'
        '<text x="66" y="119" font-family="sans-serif" font-size="18" letter-spacing="3" '
        'fill="#537A70">MODULAR FACES · TECHNICAL PILOT 01</text>'
        + "".join(svg_cards)
        + '<text x="66" y="929" font-family="sans-serif" font-size="14" fill="#537A70">'
        "3 PLAYERS / 3 STAFF · SVG LAYERS · STYLE C PALETTE · NOT FINAL ART</text></svg>"
    )
    (output / "pilot-sheet.svg").write_text(poster, encoding="utf-8")
    (output / "index.html").write_text(
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Goaletto · Портретный пилот</title><style>"
        "body{margin:0;background:#e7ddc8;color:#182b28;font:16px system-ui,sans-serif}"
        "main{max-width:1080px;margin:40px auto;padding:24px}"
        "h1{font-size:42px;margin-bottom:8px}p{max-width:720px;line-height:1.6}"
        ".grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}"
        "figure{margin:0;background:#fff9ed;padding:22px;border-radius:20px}"
        "img{width:100%;display:block}figcaption{font-weight:700;letter-spacing:2px;margin-top:16px}"
        "small{color:#537a70}@media(max-width:700px){.grid{grid-template-columns:repeat(2,1fr)}}"
        "</style><main><h1>GOALETTO / ATELIER</h1>"
        "<p>Технический пилот комбинатора: три игрока и три сотрудника. "
        "Модульные SVG-части и выбранная палитра C — Dugout. "
        "этот простой пилот не повторяет детализацию концепт-листов.</p>"
        '<div class="grid">' + "".join(cards) + "</div></main></html>",
        encoding="utf-8",
    )
    try:
        import cairosvg  # type: ignore[import-untyped]
    except (ImportError, OSError):
        print("SVG/HTML previews saved. Install CairoSVG and system Cairo for PNG export.")
    else:
        cairosvg.svg2png(bytestring=poster.encode(), write_to=str(output / "pilot-sheet.png"))
        for bank, seed in [(r["bank"], r["seed"]) for r in recipes]:
            svg = render_svg(make_recipe(seed, bank))
            cairosvg.svg2png(
                bytestring=svg.encode(),
                write_to=str(output / f"{seed}.png"),
                output_width=512,
                output_height=512,
            )
        print("Six SVG/PNG portraits, recipes, gallery and contact sheet saved.")


if __name__ == "__main__":
    main()
