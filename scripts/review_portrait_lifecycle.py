"""Audit lifecycle exports and build a static whole-person aging gallery."""

import argparse
import hashlib
import html
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont
from review_whole_portraits import face_hash

from goaletto.portraits.raster import tint_kit

ROOT = Path(__file__).resolve().parents[1] / "assets/portraits/whole-v2"
LIFE = ROOT / "lifecycle-v1"
PAPER = "#E8DECB"
INK = "#2A2824"
LABELS = {
    "player-youth": "Игрок 17–21",
    "player-adult": "Игрок 22–27",
    "player-mature": "Игрок 28–31",
    "player-veteran": "Игрок 32–38",
    "staff-early": "Персонал 32–44",
    "staff-middle": "Персонал 45–59",
    "staff-senior": "Персонал 60–70",
}
SHEET_LABELS = {
    "player-youth": "Player 17-21",
    "player-adult": "Player 22-27",
    "player-mature": "Player 28-31",
    "player-veteran": "Player 32-38",
    "staff-early": "Staff 32-44",
    "staff-middle": "Staff 45-59",
    "staff-senior": "Staff 60-70",
}
STYLE = """<style>body{background:#e8decb;color:#2a2824;font:15px system-ui;margin:24px}
a,h1,h2{color:#743d42}table{border-collapse:collapse}td,th{padding:8px;text-align:center}
tr:nth-child(even){background:#f3ecdf}img{display:block;margin:auto}
nav{display:flex;gap:16px;flex-wrap:wrap}p{max-width:980px;line-height:1.6}
</style>"""


def page(title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title>{STYLE}<h1>{html.escape(title)}</h1>"
        + body
        + "</html>\n"
    )


def main(allow_partial: bool = False) -> None:
    life = json.loads((LIFE / "manifest.json").read_text())
    if not allow_partial and life["status"] != "illustrated-lifecycle-complete":
        raise ValueError("Cannot certify an incomplete lifecycle catalog")
    base = json.loads((ROOT / "manifest.json").read_text())
    face_mask_review = []
    digests: dict[str, str] = {}
    shapes: dict[str, int] = {}
    for variant, entry in life["variants"].items():
        with (
            Image.open(ROOT / entry["image"]) as source,
            Image.open(ROOT / entry["garment_mask"]) as source_mask,
        ):
            image, mask = source.copy(), source_mask.copy()
        if image.mode != "RGBA" or image.size != (256, 256):
            raise ValueError(f"Bad portrait export: {variant}")
        if mask.mode != "L" or mask.size != image.size:
            raise ValueError(f"Bad clothing mask: {variant}")
        alpha = image.getchannel("A")
        if alpha.getextrema() != (0, 255):
            raise ValueError(f"Missing alpha: {variant}")
        top_pigments = sum(
            a >= 192 and (min(r, b) - g > 90 or min(g, b) - r > 90)
            for r, g, b, a in image.crop((0, 0, 256, 48)).get_flattened_data()
        )
        if top_pigments > 8:
            raise ValueError(f"Neighbor jersey fragment above hair: {variant}")
        values = list(alpha.get_flattened_data())
        rows = [sum(a >= 16 for a in values[y * 256 : (y + 1) * 256]) for y in range(49)]
        opaque = [sum(a >= 192 for a in values[y * 256 : (y + 1) * 256]) for y in range(48)]
        if any(
            rows[y] > 64 and rows[y + 1] < rows[y] * 0.5 and opaque[y] < 12.8 for y in range(48)
        ):
            raise ValueError(f"Faint neighbor baseline above hair: {variant}")
        digest = hashlib.sha256(image.tobytes()).hexdigest()
        if digest != entry["pixel_sha256"] or digest in digests:
            raise ValueError(f"Invalid/duplicate portrait pixels: {variant}")
        digests[digest] = variant
        shapes[variant] = face_hash(image)
        first = tint_kit(image, "#743D42", "#F2E9D8", material_mask=mask)
        second = tint_kit(image, "#3152A8", "#E3BF54", material_mask=mask)
        if first.getchannel("A").tobytes() != alpha.tobytes():
            raise ValueError(f"Recolor changes alpha: {variant}")
        diff = ImageChops.difference(first.convert("RGB"), second.convert("RGB"))
        outside = mask.point(lambda value: 255 if value == 0 else 0)
        if Image.composite(diff, Image.new("RGB", image.size), outside).getbbox():
            raise ValueError(f"Recolor outside clothing: {variant}")
        if diff.getbbox() is None:
            raise ValueError(f"No kit recoloring: {variant}")
        if mask.crop((96, 108, 160, 175)).getbbox():
            face_mask_review.append(variant)
        with Image.open(LIFE / "thumbnails" / f"{variant}.png") as thumb:
            if thumb.mode != "RGBA" or thumb.size != (96, 96):
                raise ValueError(f"Bad thumbnail: {variant}")
        if not (LIFE / "previews/portraits" / f"{variant}.webp").exists():
            raise ValueError(f"Missing gallery export: {variant}")
    groups: dict[str, list[str]] = {}
    for identity in sorted(life["identities"]):
        original = base["identities"][identity]["variants"][0]
        origin = Path(base["variants"][original]["source"]).stem
        groups.setdefault(origin, []).append(identity)
    font = ImageFont.load_default(size=12)
    links = []
    continuity = []
    for group, identities in groups.items():
        originals = {}
        for identity in identities:
            variant = base["identities"][identity]["variants"][0]
            with Image.open(ROOT / base["variants"][variant]["image"]) as original:
                originals[identity] = face_hash(original.convert("RGBA"))
        for identity in identities:
            for variant in life["identities"][identity].values():
                distances = sorted(
                    ((shapes[variant] ^ shape).bit_count(), other)
                    for other, shape in originals.items()
                )
                own_distance = (shapes[variant] ^ originals[identity]).bit_count()
                own_rank = next(
                    i + 1 for i, (_, other) in enumerate(distances) if other == identity
                )
                continuity.append(
                    {
                        "variant": variant,
                        "identity_id": identity,
                        "own_rank": own_rank,
                        "own_distance": own_distance,
                        "closest_identity": distances[0][1],
                        "margin": own_distance - distances[0][0],
                    }
                )
        stages = [
            s["id"] for s in life["stages"] if s["bank"] == "staff" or group.startswith("players")
        ]
        sheet = Image.new("RGB", ((len(stages) + 1) * 116, (len(identities) + 1) * 110), PAPER)
        draw = ImageDraw.Draw(sheet)
        draw.text((4, 10), group, fill=INK, font=font)
        for col, stage in enumerate(stages, 1):
            draw.text((col * 116 + 4, 10), SHEET_LABELS[stage], fill=INK, font=font)
        rows = []
        for row, identity in enumerate(identities, 1):
            short = identity.rsplit("/", 1)[-1]
            draw.text((4, row * 110 + 40), short, fill=INK, font=font)
            cells = [f"<th>{html.escape(short)}</th>"]
            for col, stage in enumerate(stages, 1):
                variant = life["identities"][identity].get(stage)
                if variant is None:
                    cells.append("<td>Ещё нет</td>")
                    continue
                with Image.open(LIFE / "thumbnails" / f"{variant}.png") as thumb:
                    sheet.paste(thumb, (col * 116 + 10, row * 110), thumb)
                safe = html.escape(variant)
                cells.append(
                    f'<td><a href="previews/portraits/{safe}.webp"><img loading="lazy" '
                    f'src="thumbnails/{safe}.png" width="96" height="96" '
                    f'alt="{short}: {html.escape(LABELS[stage])}"></a></td>'
                )
            rows.append("<tr>" + "".join(cells) + "</tr>")
        sheet.save(LIFE / "previews" / f"{group}-aging.webp", quality=90, method=4)
        headers = "".join(f"<th>{LABELS[stage]}</th>" for stage in stages)
        body = (
            '<nav><a href="index.html">Весь каталог</a>'
            f'<a href="previews/{group}-aging.webp">Сравнение одним файлом</a></nav>'
            "<p>Одна строка — один человек. Возрастные границы относятся к внешности, "
            "а не к правилам найма. Форма на этой странице бордовая; цвета клуба "
            "меняются при локальном рендере.</p>"
            f"<table><tr><th>ID</th>{headers}</tr>{''.join(rows)}</table>"
        )
        (LIFE / f"{group}.html").write_text(page(f"Dugout · {group}", body))
        links.append(f'<li><a href="{group}.html">{group} · {len(identities)} лиц</a></li>')
    ranked = sorted(continuity, key=lambda r: (r["margin"], r["own_distance"]), reverse=True)
    review = Image.new("RGB", (8 * 160, 8 * 188), PAPER)
    draw = ImageDraw.Draw(review)
    for index, item in enumerate(ranked[:32]):
        original = base["identities"][item["identity_id"]]["variants"][0]
        for side in (0, 1):
            variant = original if side == 0 else item["variant"]
            path = (ROOT / "thumbnails" if side == 0 else LIFE / "thumbnails") / f"{variant}.png"
            with Image.open(path) as thumb:
                col, row = (index * 2 + side) % 8, (index * 2 + side) // 8
                enlarged = thumb.resize((144, 144), Image.Resampling.LANCZOS)
                review.paste(enlarged, (col * 160 + 8, row * 188), enlarged)
                label = "base" if side == 0 else life["variants"][variant]["stage"]
                draw.text((col * 160 + 6, row * 188 + 146), variant.split("-")[0], fill=INK)
                draw.text((col * 160 + 6, row * 188 + 164), label, fill=INK)
    review.save(LIFE / "previews/continuity-review.png", optimize=True)
    examples = [
        identity
        for identity in ("dugout-whole-v2/p0001", "dugout-whole-v2/p0002")
        if len(life["identities"].get(identity, {})) == 7
    ]
    if examples:
        sample = Image.new("RGB", (8 * 140, 48 + len(examples) * 160), PAPER)
        draw = ImageDraw.Draw(sample)
        for col, stage in enumerate(life["stages"], 1):
            draw.text((col * 140 + 6, 16), SHEET_LABELS[stage["id"]], fill=INK)
        for row, identity in enumerate(examples):
            draw.text((8, 48 + row * 160 + 62), identity.rsplit("/", 1)[-1], fill=INK)
            for col, stage in enumerate(life["stages"], 1):
                variant = life["identities"][identity][stage["id"]]
                with Image.open(LIFE / "previews/portraits" / f"{variant}.webp") as portrait:
                    portrait = portrait.resize((140, 140), Image.Resampling.LANCZOS)
                    sample.paste(portrait, (col * 140, 48 + row * 160), portrait)
        sample.save(LIFE / "previews/career-examples.png", optimize=True)
    report = {
        "checked_variants": len(life["variants"]),
        "identities": len(life["identities"]),
        "status": life["status"],
        "alpha_and_pixels_outside_mask_preserved": True,
        "all_garments_change_color": True,
        "exact_duplicate_pixels": 0,
        "no_technical_jersey_fragments_above_hair": True,
        "no_faint_neighbor_baselines_above_hair": True,
        "central_face_mask_review": face_mask_review,
        "similarity_policy": "Slight likeness between different faces accepted by user",
        "visual_limits": "Technical checks do not certify same-person visual continuity",
        "continuity_review": ranked[:32],
        "continuity_ranking_policy": (
            "Coarse shape distance against the 20 original faces in the same atlas; "
            "review aid only, not an identity classifier or automatic rejection"
        ),
    }
    (LIFE / "quality-report.json").write_text(json.dumps(report, indent=2) + "\n")
    note = (
        "<p><strong>Неполный набор.</strong> Часть возрастных версий ещё не создана. "
        "Пустые ячейки обозначают отсутствующие иллюстрации. Исходный банк из "
        "1 000 игроков и 80 сотрудников сохранён полностью.</p>"
        if life["status"] != "illustrated-lifecycle-complete"
        else "<p>Все запланированные возрастные версии подготовлены.</p>"
    )
    body = (
        f"<p>{len(life['identities'])} внешностей, {len(life['variants'])} возрастных "
        "и карьерных вариантов. Четыре группы футболистов и три группы персонала. "
        "Все возраста внутри группы используют одну иллюстрацию. Бывший игрок "
        "сохраняет ID лица. Назначенные людям лица и исторические варианты сохраняются.</p>"
        + note
        + '<nav><a href="../index.html">Исходный банк</a>'
        '<a href="quality-report.json">Техническая проверка</a>'
        '<a href="previews/continuity-review.png">Проверка узнаваемости</a></nav>'
        "<ul>" + "".join(links) + "</ul>"
    )
    (LIFE / "index.html").write_text(page("Goaletto · Dugout · возраст и карьера", body))
    print(json.dumps({k: v for k, v in report.items() if k != "continuity_review"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-partial", action="store_true")
    main(parser.parse_args().allow_partial)
