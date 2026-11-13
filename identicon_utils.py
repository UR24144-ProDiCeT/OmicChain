"""Deterministic identicon (Blockies) generated from a hexadecimal hash."""

import colorsys
import random
from pathlib import Path

from PIL import Image, ImageDraw

__all__ = ["generate_blockies", "generate_identicon"]


def _seed_from_hash(hash_hex: str) -> int:
    """Derive a deterministic integer seed from a hexadecimal hash."""
    h = hash_hex.lower().replace("0x", "")
    if len(h) < 32:
        raise ValueError("Hash must contain at least 32 hexadecimal characters.")
    try:
        return int(h[:32], 16)
    except ValueError as exc:
        raise ValueError("Hash contains non-hexadecimal characters.") from exc


def _hsl_to_rgb(h: float, s: float, l: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return int(r * 255), int(g * 255), int(b * 255)


def generate_blockies(hash_hex: str, grid_size: int = 8, scale: int = 32) -> Image.Image:
    """
    Generate a deterministic Blockies-style identicon from a hash.

    The pattern is horizontally symmetric and uses three deterministic
    colors. For a given hash, the generated image is always identical.
    """
    rng = random.Random(_seed_from_hash(hash_hex))

    def rand_color():
        hue = rng.random()
        sat = 0.5 + rng.random() * 0.4
        light = 0.4 + rng.random() * 0.3
        return _hsl_to_rgb(hue, sat, light)

    bg_color = rand_color()
    main_color = rand_color()
    spot_color = rand_color()

    half = (grid_size + 1) // 2
    cells = [[0] * grid_size for _ in range(grid_size)]

    for x in range(half):
        for y in range(grid_size):
            v = rng.random()
            if v > 0.66:
                value = 2
            elif v > 0.33:
                value = 1
            else:
                value = 0
            cells[y][x] = value
            cells[y][grid_size - 1 - x] = value

    img = Image.new("RGB", (grid_size * scale, grid_size * scale), bg_color)
    draw = ImageDraw.Draw(img)

    for y in range(grid_size):
        for x in range(grid_size):
            value = cells[y][x]
            if value == 0:
                continue
            color = main_color if value == 1 else spot_color
            draw.rectangle(
                [
                    x * scale,
                    y * scale,
                    (x + 1) * scale - 1,
                    (y + 1) * scale - 1,
                ],
                fill=color,
            )

    return img


def generate_identicon(hash_hex: str, output_path=None) -> Image.Image:
    """Generate one Blockies identicon and optionally save it."""
    image = generate_blockies(hash_hex)
    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path)
    return image


if __name__ == "__main__":
    import sys

    test_hash = sys.argv[1] if len(sys.argv) > 1 else "a" * 64
    outdir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")
    output = outdir / "identicon_blockies.png"
    generate_identicon(test_hash, output)
    print(output)
