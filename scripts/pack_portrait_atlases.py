"""Import whole-person atlases; no facial-part assembly or image generation here."""

import argparse
import hashlib
import json
from collections import deque
from pathlib import Path
from typing import cast

from pack_whole_portraits import garment_mask as pigment_mask
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from goaletto.portraits.whole import recipe_for_variant, render_whole

ROOT = Path(__file__).resolve().parents[1] / "assets/portraits/whole-v2"
PAPER = "#E8DECB"


def garment_mask(image: Image.Image) -> Image.Image:
    """Keep technical pigment regions connected to the bottom garment, excluding lips."""
    raw = pigment_mask(image)
    values = list(raw.get_flattened_data())
    expanded = list(raw.filter(ImageFilter.MaxFilter(7)).get_flattened_data())
    width, height = image.size
    reachable = bytearray(width * height)
    queue: deque[int] = deque()
    bounds = raw.getbbox()
    if bounds is None or bounds[3] < round(height * 0.8):
        raise ValueError("No technical garment pigments in lower torso")
    for index in range((bounds[3] - 8) * width, width * height):
        if values[index]:
            reachable[index] = 1
            queue.append(index)
    while queue:
        index = queue.popleft()
        x, y = index % width, index // width
        neighbors = []
        if x:
            neighbors.append(index - 1)
        if x < width - 1:
            neighbors.append(index + 1)
        if y:
            neighbors.append(index - width)
        if y < height - 1:
            neighbors.append(index + width)
        for neighbor in neighbors:
            if expanded[neighbor] and not reachable[neighbor]:
                reachable[neighbor] = 1
                queue.append(neighbor)
    raw.putdata([value if reachable[index] else 0 for index, value in enumerate(values)])
    return raw


def clean_person(sprite: Image.Image) -> Image.Image:
    """Drop disconnected neighbor fragments; keep the complete connected portrait."""
    alpha = sprite.getchannel("A")
    values = list(alpha.get_flattened_data())
    width, height = sprite.size
    row_counts = [
        sum(value >= 16 for value in values[row * width : (row + 1) * width])
        for row in range(height)
    ]
    # A previous row's horizontal torso edge may intrude at the tile top. Its
    # abrupt wide-to-empty step distinguishes it from the next person's hair.
    top_edges = [
        row
        for row in range(height // 30)
        if row_counts[row] >= width * 0.65 and row_counts[row + 1] < width * 0.3
    ]
    if top_edges:
        values[: (top_edges[-1] + 1) * width] = [0] * ((top_edges[-1] + 1) * width)
    # Atlases have broad, straight shoulder bases. Neighbor hair can touch their
    # antialiased edge across a narrow gutter; clip below the last wide torso row.
    wide_rows = [row for row in range(height // 2, height) if row_counts[row] >= width * 0.65]
    if not wide_rows:
        raise ValueError("Atlas portrait lacks the expected broad shoulder base")
    bottom = wide_rows[-1] + 1
    values[bottom * width :] = [0] * ((height - bottom) * width)
    visited = bytearray(len(values))
    largest: list[int] = []
    for start, value in enumerate(values):
        if value < 16 or visited[start]:
            continue
        visited[start] = 1
        queue = deque([start])
        component = []
        while queue:
            index = queue.popleft()
            component.append(index)
            x, y = index % width, index // width
            neighbors = []
            if x:
                neighbors.append(index - 1)
            if x < width - 1:
                neighbors.append(index + 1)
            if y:
                neighbors.append(index - width)
            if y < height - 1:
                neighbors.append(index + width)
            for neighbor in neighbors:
                if not visited[neighbor] and values[neighbor] >= 16:
                    visited[neighbor] = 1
                    queue.append(neighbor)
        if len(component) > len(largest):
            largest = component
    cleaned = [0] * len(values)
    for index in largest:
        cleaned[index] = values[index]
    alpha.putdata(cleaned)
    sprite.putalpha(alpha)
    return sprite


def normalize_garment_edges(image: Image.Image) -> Image.Image:
    """Normalize highly saturated red/blue garment fringes into technical pigments."""
    pixels = []
    for index, pixel in enumerate(image.get_flattened_data()):
        red, green, blue, alpha = cast(tuple[int, int, int, int], pixel)
        if alpha >= 16 and index // image.width >= round(image.height * 0.65):
            if red >= 90 and green < red * 0.18 and blue < red * 0.5:
                green, blue = 0, red
            elif blue >= 90 and red < blue * 0.18 and green < blue * 0.5:
                red, green = 0, blue
        pixels.append((red, green, blue, alpha))
    image.putdata(pixels)
    return image


def pack(require_complete: bool = False) -> None:
    plan = json.loads((ROOT / "production-plan.json").read_text())
    for folder in ("portraits", "masks", "thumbnails", "previews"):
        (ROOT / folder).mkdir(exist_ok=True)
    catalog = {
        "catalog_version": 2,
        "status": "whole-person-library-awaiting-visual-review",
        "sizes": [96, 256],
        "banks": {"players": [], "staff": []},
        "identities": {},
        "variants": {},
    }
    hashes: dict[str, str] = {}
    missing = []
    old_path = ROOT / "manifest.json"
    old = json.loads(old_path.read_text()) if old_path.exists() else {"variants": {}}
    changed_atlases: set[str] = set()
    for atlas in plan["atlases"]:
        path = ROOT / "sources" / f"{atlas['name']}.png"
        if not path.exists():
            missing.append(atlas["name"])
            continue
        with Image.open(path) as source:
            if source.mode != "RGBA" or source.getchannel("A").getextrema()[0] != 0:
                raise ValueError(f"Atlas lacks actual transparency: {path.name}")
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
                sprite = clean_person(source.crop(crop))
                # Ignore generated almost-transparent RGB speckles in gutters.
                alpha = sprite.getchannel("A")
                alpha = alpha.point(lambda value: 0 if value < 16 else value)
                sprite.putalpha(alpha)
                bbox = alpha.getbbox()
                if bbox is None or bbox[3] - bbox[1] < sprite.height // 2:
                    raise ValueError(
                        f"Missing/undersized whole person: {atlas['name']}/{cell['slot']}"
                    )
                # Crop one coherent person, not facial parts; never resize axes separately.
                sprite = ImageOps.contain(sprite.crop(bbox), (248, 248), Image.Resampling.LANCZOS)
                packed = Image.new("RGBA", (256, 256))
                packed.alpha_composite(sprite, ((256 - sprite.width) // 2, 256 - sprite.height))
                packed = normalize_garment_edges(packed)
                digest = hashlib.sha256(packed.tobytes()).hexdigest()
                if digest in hashes:
                    raise ValueError(f"Exact duplicate face: {cell['identity']} / {hashes[digest]}")
                hashes[digest] = cell["identity"]
                identity = f"dugout-whole-v2/{cell['identity']}"
                bank = atlas["bank"]
                role = "player" if bank == "players" else "staff"
                variant = f"{cell['identity']}-{role}{cell['age']}"
                image_path = f"portraits/{variant}.png"
                mask_path = f"masks/{variant}.png"
                mask = garment_mask(packed)
                if mask.getbbox() is None:
                    raise ValueError(f"No clothing mask: {variant}")
                previous = old["variants"].get(variant, {})
                same_image = previous.get("pixel_sha256") == digest and (ROOT / image_path).exists()
                same_mask = False
                if (ROOT / mask_path).exists():
                    with Image.open(ROOT / mask_path) as existing:
                        same_mask = existing.tobytes() == mask.tobytes()
                if not same_image:
                    packed.save(ROOT / image_path, optimize=True)
                if not same_mask:
                    mask.save(ROOT / mask_path, optimize=True)
                if not same_image or not same_mask:
                    changed_atlases.add(atlas["name"])
                catalog["banks"][bank].append(variant)
                catalog["identities"][identity] = {"variants": [variant]}
                catalog["variants"][variant] = {
                    "identity_id": identity,
                    "bank": bank,
                    "depicted_age": cell["age"],
                    "image": image_path,
                    "garment_mask": mask_path,
                    "source": f"sources/{atlas['name']}.png",
                    "source_crop": list(crop),
                    "prompt": f"prompts/{atlas['name']}.txt",
                    "pixel_sha256": digest,
                    "review_status": "awaiting-visual-review",
                    "age_policy": "One age; full age/career variants remain future work",
                }
    if require_complete and missing:
        raise ValueError(f"Missing source atlases: {missing}")
    if require_complete:
        assert len(catalog["banks"]["players"]) == 1000
        assert len(catalog["banks"]["staff"]) == 80
    (ROOT / "manifest.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
    font = ImageFont.load_default(size=14)
    for atlas in plan["atlases"]:
        if atlas["name"] in missing:
            continue
        if atlas["name"] not in changed_atlases and all(
            (ROOT / "previews" / f"{atlas['name']}{suffix}.png").exists()
            for suffix in ("", "-96px")
        ):
            continue
        sheet = Image.new("RGB", (5 * 192, 4 * 218), PAPER)
        small_sheet = Image.new("RGB", (5 * 112, 4 * 120), PAPER)
        for cell in atlas["cells"]:
            role = "player" if atlas["bank"] == "players" else "staff"
            variant = f"{cell['identity']}-{role}{cell['age']}"
            recipe = recipe_for_variant(variant, ROOT)
            image = render_whole(recipe, kit_primary="#743D42", kit_secondary="#F2E9D8", root=ROOT)
            col, row = cell["slot"] % 5, cell["slot"] // 5
            preview = image.resize((192, 192), Image.Resampling.LANCZOS)
            sheet.paste(preview, (col * 192, row * 218), preview)
            ImageDraw.Draw(sheet).text(
                (col * 192 + 12, row * 218 + 195), variant, font=font, fill="#2A2824"
            )
            thumb = image.resize((96, 96), Image.Resampling.LANCZOS)
            thumb.save(ROOT / "thumbnails" / f"{variant}.png", optimize=True)
            small_sheet.paste(thumb, (col * 112 + 8, row * 120), thumb)
            ImageDraw.Draw(small_sheet).text(
                (col * 112 + 16, row * 120 + 98), cell["identity"], font=font, fill="#2A2824"
            )
        sheet.save(ROOT / "previews" / f"{atlas['name']}.png", optimize=True)
        small_sheet.save(ROOT / "previews" / f"{atlas['name']}-96px.png", optimize=True)
    sizes = [path.stat().st_size for path in (ROOT / "portraits").glob("*.png")]
    report = {
        "players": len(catalog["banks"]["players"]),
        "staff": len(catalog["banks"]["staff"]),
        "missing_atlases": missing,
        "exact_duplicate_pixels": 0,
        "total_png_bytes": sum(sizes),
        "mean_png_bytes": round(sum(sizes) / len(sizes)) if sizes else 0,
        "limits": "Pixel uniqueness is not proof of visual distinction; review sheets required",
    }
    (ROOT / "import-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-complete", action="store_true")
    pack(parser.parse_args().require_complete)
