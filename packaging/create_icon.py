"""Generate deterministic Windows application icons for local/CI packaging."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def create_icon(output: Path) -> None:
    scale = 4
    size = 256 * scale
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = 18 * scale
    radius = 52 * scale
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=radius,
        fill=(20, 33, 54, 255),
    )

    # Three connected routes form a compact W / branching-workflow mark.
    stroke = 17 * scale
    route = (103, 157, 255, 255)
    highlight = (112, 220, 190, 255)
    points = [
        (55, 78), (87, 184), (128, 104), (169, 184), (201, 78),
    ]
    scaled = [(x * scale, y * scale) for x, y in points]
    draw.line(scaled, fill=route, width=stroke, joint="curve")
    branch = [(128 * scale, 104 * scale), (128 * scale, 55 * scale)]
    draw.line(branch, fill=highlight, width=stroke, joint="curve")
    for x, y in scaled + [branch[-1]]:
        r = 11 * scale
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(244, 248, 255, 255))

    output.parent.mkdir(parents=True, exist_ok=True)
    image.resize((256, 256), Image.Resampling.LANCZOS).save(
        output,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    create_icon(args.output)


if __name__ == "__main__":
    main()
