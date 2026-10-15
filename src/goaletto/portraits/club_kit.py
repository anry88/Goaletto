"""A small local equipment illustration in the club's actual two colors."""

import re
from io import BytesIO

from PIL import Image, ImageDraw

POLICY = "club-kit-v3"


def render_club_kit(primary: str, secondary: str) -> bytes:
    if any(re.fullmatch(r"#[0-9A-Fa-f]{6}", color) is None for color in (primary, secondary)):
        raise ValueError("Invalid kit colors")
    scale = 3
    image = Image.new("RGB", (256 * scale, 256 * scale), "#F3EBD9")
    d = ImageDraw.Draw(image)

    def polygon(points: list[tuple[int, int]], fill: str) -> None:
        d.polygon(
            [(x * scale, y * scale) for x, y in points],
            fill=fill,
            outline="#293A34",
            width=2 * scale,
        )

    # Shirt, contrasting cuffs/collar; shorts and two socks complete the kit.
    polygon(
        [
            (89, 24),
            (109, 17),
            (128, 29),
            (147, 17),
            (167, 24),
            (199, 53),
            (181, 77),
            (166, 66),
            (166, 132),
            (90, 132),
            (90, 66),
            (75, 77),
            (57, 53),
        ],
        primary,
    )
    polygon([(109, 17), (128, 29), (147, 17), (141, 32), (128, 40), (115, 32)], secondary)
    polygon([(57, 53), (75, 77), (81, 71), (63, 47)], secondary)
    polygon([(175, 71), (181, 77), (199, 53), (193, 47)], secondary)
    polygon(
        [(91, 142), (165, 142), (172, 188), (137, 188), (128, 167), (119, 188), (84, 188)],
        secondary,
    )
    d.line([(91 * scale, 147 * scale), (165 * scale, 147 * scale)], fill=primary, width=5 * scale)
    # Keep the shirt/shorts proportions, draw the long socks directly at final
    # proportions instead of squashing a pair into a short thumbnail.
    strip = Image.new("RGB", (256 * scale, 112 * scale), "#F3EBD9")
    for box, size, position in (
        ((52, 12, 203, 135), (126, 103), (8, 6)),
        ((80, 139, 177, 191), (78, 42), (153, 6)),
    ):
        garment = image.crop((box[0] * scale, box[1] * scale, box[2] * scale, box[3] * scale))
        garment = garment.resize(tuple(v * scale for v in size), Image.Resampling.LANCZOS)
        strip.paste(garment, (position[0] * scale, position[1] * scale))
    image = strip
    d = ImageDraw.Draw(image)
    for left in (162, 202):
        polygon(
            [
                (left, 59),
                (left + 18, 59),
                (left + 17, 91),
                (left + 24, 97),
                (left + 24, 103),
                (left + 2, 103),
                (left - 1, 98),
            ],
            primary,
        )
        polygon(
            [(left, 59), (left + 18, 59), (left + 18, 65), (left, 65)],
            secondary,
        )
    output = BytesIO()
    strip.resize((256, 112), Image.Resampling.LANCZOS).save(output, format="PNG")
    return output.getvalue()
