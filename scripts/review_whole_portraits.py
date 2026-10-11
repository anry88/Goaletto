"""Build an offline art gallery and technical audit; similarity scores require human review."""

import hashlib
import heapq
import html
import json
from itertools import combinations
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from goaletto.portraits.raster import tint_kit

ROOT = Path(__file__).resolve().parents[1] / "assets/portraits/whole-v2"
PAPER = "#E8DECB"
INK = "#2A2824"


def face_hash(image: Image.Image) -> int:
    """A coarse shape hash for review ranking, not an identity classifier."""
    background = Image.new("RGBA", image.size, PAPER)
    background.alpha_composite(image)
    face = background.crop((40, 24, 216, 190)).convert("L").resize((17, 16))
    values = list(face.get_flattened_data())
    result = 0
    for row in range(16):
        for col in range(16):
            result = (result << 1) | int(values[row * 17 + col] > values[row * 17 + col + 1])
    return result


def contact_sheet(variants: list[str], filename: str, cols: int = 8) -> None:
    sheet = Image.new("RGB", (cols * 128, ((len(variants) + cols - 1) // cols) * 150), PAPER)
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=13)
    for index, variant in enumerate(variants):
        with Image.open(ROOT / "thumbnails" / f"{variant}.png") as thumb:
            col, row = index % cols, index // cols
            sheet.paste(thumb, (col * 128 + 16, row * 150 + 4), thumb)
        draw.text((col * 128 + 16, row * 150 + 105), variant.split("-")[0], font=font, fill=INK)
    sheet.save(ROOT / "previews" / filename, optimize=True)


def main() -> None:
    catalog = json.loads((ROOT / "manifest.json").read_text())
    entries = catalog["variants"]
    (ROOT / "previews/portraits").mkdir(parents=True, exist_ok=True)
    hashes = {}
    face_pixels = {}
    exact_heads: list[list[str]] = []
    face_mask_overlap = []
    unchanged_garments = []
    for variant, entry in entries.items():
        with Image.open(ROOT / entry["image"]) as source:
            image = source.convert("RGBA")
        with Image.open(ROOT / entry["garment_mask"]) as source_mask:
            mask = source_mask.copy()
        if mask.mode != "L" or mask.size != (256, 256) or image.size != (256, 256):
            raise ValueError(f"Bad export: {variant}")
        digest = hashlib.sha256(image.tobytes()).hexdigest()
        if digest != entry["pixel_sha256"]:
            raise ValueError(f"Manifest hash mismatch: {variant}")
        first = tint_kit(image, "#743D42", "#F2E9D8", material_mask=mask)
        first.save(ROOT / "previews/portraits" / f"{variant}.webp", quality=90, method=4)
        second = tint_kit(image, "#3152A8", "#E3BF54", material_mask=mask)
        if first.getchannel("A").tobytes() != image.getchannel("A").tobytes():
            raise ValueError(f"Alpha changed: {variant}")
        changed = False
        for index, (left, right, material) in enumerate(
            zip(
                first.get_flattened_data(),
                second.get_flattened_data(),
                mask.get_flattened_data(),
                strict=True,
            )
        ):
            if not material and left != right:
                raise ValueError(f"Outside-mask recolor: {variant}/{index}")
            if left != right:
                changed = True
        if not changed:
            unchanged_garments.append(variant)
        # Central lower face is a conservative diagnostic, not automatic art rejection.
        if mask.crop((96, 108, 160, 175)).getbbox() is not None:
            face_mask_overlap.append(variant)
        head = image.crop((40, 24, 216, 175))
        head_digest = hashlib.sha256(head.tobytes()).hexdigest()
        if head_digest in face_pixels:
            exact_heads.append([face_pixels[head_digest], variant])
        face_pixels[head_digest] = variant
        hashes[variant] = face_hash(image)
    if unchanged_garments:
        raise ValueError(f"Garment does not change color: {unchanged_garments}")
    nearest: list[tuple[int, str, str]] = []
    for left, right in combinations(hashes, 2):
        distance = (hashes[left] ^ hashes[right]).bit_count()
        item = (-distance, left, right)
        if len(nearest) < 24:
            heapq.heappush(nearest, item)
        elif item > nearest[0]:
            heapq.heapreplace(nearest, item)
    pairs = [
        {"distance": -distance, "left": left, "right": right}
        for distance, left, right in sorted(nearest, reverse=True)
    ]
    contact_sheet(
        [variant for pair in pairs for variant in (pair["left"], pair["right"])],
        "closest-pairs.png",
    )
    for bank in ("players", "staff"):
        variants = catalog["banks"][bank]
        for start in range(0, len(variants), 200):
            contact_sheet(
                variants[start : start + 200], f"{bank}-overview-{start // 200 + 1}.png", 10
            )
    contact_sheet(catalog["banks"]["players"][::63], "players-sample.png")
    contact_sheet(catalog["banks"]["staff"][::5], "staff-sample.png")
    colors = [("#743D42", "#F2E9D8"), ("#315B49", "#E2BF57"), ("#3152A8", "#F2E9D8")]
    examples = [
        catalog["banks"]["players"][0],
        catalog["banks"]["players"][499],
        catalog["banks"]["staff"][0],
        catalog["banks"]["staff"][79],
    ]
    kits = Image.new("RGB", (3 * 192, len(examples) * 216), PAPER)
    for row, variant in enumerate(examples):
        entry = entries[variant]
        with (
            Image.open(ROOT / entry["image"]) as source,
            Image.open(ROOT / entry["garment_mask"]) as mask,
        ):
            for col, (primary, secondary) in enumerate(colors):
                preview = tint_kit(source.convert("RGBA"), primary, secondary, material_mask=mask)
                preview = preview.resize((192, 192), Image.Resampling.LANCZOS)
                kits.paste(preview, (col * 192, row * 216), preview)
                ImageDraw.Draw(kits).text((col * 192 + 12, row * 216 + 194), variant, fill=INK)
    kits.save(ROOT / "previews/kit-colors.png", optimize=True)
    report = {
        "checked_variants": len(entries),
        "all_garments_change_color": True,
        "alpha_and_pixels_outside_mask_preserved": True,
        "exact_duplicate_head_crops": exact_heads,
        "central_face_mask_review": face_mask_overlap,
        "closest_shape_pairs": pairs,
        "visual_review": (
            "Slight likeness accepted by user; shape ranking is not proof of distinct identity"
        ),
        "age_variants": (
            "Original depictions preserved; lifecycle-v1 contains illustrated age/career stages"
        ),
    }
    (ROOT / "quality-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    sections = []
    for bank, title in (("players", "Футболисты — 1 000"), ("staff", "Персонал — 80")):
        cards = []
        for variant in catalog["banks"][bank]:
            entry = entries[variant]
            label = html.escape(variant)
            cards.append(
                f'<a class="card" href="previews/portraits/{label}.webp"><img loading="lazy" '
                f'src="thumbnails/{label}.png" width="96" height="96" alt="{label}">'
                f"<span>{label}</span><small>Внешность ≈ {entry['depicted_age']} лет</small></a>"
            )
        sections.append(
            f'<section id="{bank}"><h2>{title}</h2>'
            f'<div class="grid">{"".join(cards)}</div></section>'
        )
    document = """<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Goaletto · Dugout</title>
<style>body{margin:0;background:#e8decb;color:#2a2824;font:16px system-ui}
main{max-width:1250px;margin:auto;padding:24px}h1,h2{color:#743d42}a{color:#743d42}
nav{display:flex;gap:24px;flex-wrap:wrap}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(145px,1fr));gap:12px}
.card{display:flex;align-items:center;flex-direction:column;background:#f3ecdf;padding:10px;text-decoration:none;border-radius:8px}
.card span{font-size:13px}.card small{font-size:11px;color:#67624f;margin-top:6px}
.note{max-width:900px;line-height:1.6}
</style><main><h1>Goaletto · Dugout</h1>
<p class="note">1 000 цельных портретов футболистов для 50 клубов по 20 игроков
и 80 портретов персонала.
Это библиотека иллюстраций для проверки, персонажи ещё не назначены в игровой БД.
Указан изображённый возраст, а не утверждённый возраст стартового состава.
Возрастные и карьерные версии представлены в отдельной галерее.
Нажмите портрет для предпросмотра 256×256; миниатюры ниже 96×96.</p>
<nav><a href="#players">Игроки</a><a href="#staff">Персонал</a>
<a href="lifecycle-v1/index.html">Возраст и карьера</a>
<a href="previews/kit-colors.png">Цвета формы</a>
<a href="previews/closest-pairs.png">24 близкие по форме пары</a>
<a href="quality-report.json">Отчёт проверки</a></nav>
"""
    (ROOT / "index.html").write_text(document + "".join(sections) + "</main></html>\n")
    print(json.dumps({key: value for key, value in report.items() if key != "closest_shape_pairs"}))


if __name__ == "__main__":
    main()
