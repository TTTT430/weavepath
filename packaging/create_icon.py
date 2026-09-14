"""Generate deterministic Windows application icons for local/CI packaging."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def create_icon(output: Path) -> None:
    scale = 4
    size = 256 * scale
    # Transparent canvas; the branching W is the complete application mark.
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Three connected routes form a compact W / branching-workflow mark.
    stroke = 19 * scale
    route = (65, 123, 240, 255)
    highlight = (27, 184, 164, 255)
    points = [
        (28, 68), (73, 207), (128, 112), (183, 207), (228, 68),
    ]
    scaled = [(x * scale, y * scale) for x, y in points]
    draw.line(scaled, fill=route, width=stroke, joint="curve")
    branch = [(128 * scale, 112 * scale), (128 * scale, 35 * scale)]
    draw.line(branch, fill=highlight, width=stroke, joint="curve")
    for index, (x, y) in enumerate(scaled + [branch[-1]]):
        r = 13 * scale
        fill = highlight if index == len(scaled) else route
        draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)
        inner = 4 * scale
        draw.ellipse((x - inner, y - inner, x + inner, y + inner), fill=(255, 255, 255, 255))

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
