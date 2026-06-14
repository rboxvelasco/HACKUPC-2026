#!/usr/bin/env python3
"""
Render bitmap_occupied and bitmap_gap as a PNG for visual inspection.

Usage:
    python3 bitmap_preview.py <case_dir> <solution_csv> [output.png]

Produces a side-by-side image:
    - left:  occupied (obstacles + bay bodies) in dark grey
    - right: gap zones in blue
    - combined overlay (rightmost): occupied = grey, gap = blue

Y-axis is flipped so row 0 (y=0 world) is at the bottom, matching the HTML
visualizations.
"""

import sys
import numpy as np

from src.core.bitmap import rasterize_solution


def render_png(case_dir: str, solution_csv: str, out_path: str) -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("This preview needs Pillow. Install with: pip install Pillow")
        sys.exit(1)

    r = rasterize_solution(case_dir, solution_csv)
    occ = r.bitmap_occupied
    gap = r.bitmap_gap
    rows, cols = r.shape

    # Upscale so cells are visible. Target ~600px on the longest side.
    target = 600
    scale = max(1, min(target // max(rows, cols), 10))

    # Flip vertically so y=0 is at the bottom (matches world coords)
    occ_img = np.flipud(occ)
    gap_img = np.flipud(gap)

    def to_rgb_occupied(mask: np.ndarray) -> np.ndarray:
        img = np.full((*mask.shape, 3), 245, dtype=np.uint8)  # bg light grey
        img[mask] = [50, 50, 55]                              # occupied dark
        return img

    def to_rgb_gap(mask: np.ndarray) -> np.ndarray:
        img = np.full((*mask.shape, 3), 245, dtype=np.uint8)
        img[mask] = [0, 113, 227]                              # gap blue
        return img

    def to_rgb_combined(occ: np.ndarray, gap: np.ndarray) -> np.ndarray:
        img = np.full((*occ.shape, 3), 245, dtype=np.uint8)
        img[gap] = [180, 210, 255]                             # gap light blue
        img[occ] = [50, 50, 55]                                # occupied dark
        return img

    panels = [
        ("Occupied (obstacles + bays)", to_rgb_occupied(occ_img)),
        ("Gap zones", to_rgb_gap(gap_img)),
        ("Combined", to_rgb_combined(occ_img, gap_img)),
    ]

    # Upscale each panel
    panel_imgs = []
    for title, rgb in panels:
        img = Image.fromarray(rgb)
        img = img.resize(
            (rgb.shape[1] * scale, rgb.shape[0] * scale),
            resample=Image.NEAREST,
        )
        panel_imgs.append((title, img))

    # Compose side-by-side with titles
    pad = 16
    title_h = 28
    panel_w = panel_imgs[0][1].width
    panel_h = panel_imgs[0][1].height
    total_w = pad + (panel_w + pad) * len(panel_imgs)
    total_h = pad + title_h + panel_h + pad

    canvas = Image.new('RGB', (total_w, total_h), (255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
    except (OSError, IOError):
        font = ImageFont.load_default()

    x = pad
    for title, img in panel_imgs:
        draw.text((x, pad // 2), title, fill=(30, 30, 30), font=font)
        canvas.paste(img, (x, pad + title_h))
        x += panel_w + pad

    # Footer with metadata
    meta = (
        f"cell={r.cell_size}mm  shape={rows}×{cols}  "
        f"occ={int(occ.sum()):,} cells ({100*occ.mean():.1f}%)  "
        f"gap={int(gap.sum()):,} cells ({100*gap.mean():.1f}%)"
    )
    draw.text((pad, total_h - pad - 4), meta, fill=(120, 120, 120), font=font)

    canvas.save(out_path)
    print(f"Preview written to {out_path}")


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python3 bitmap_preview.py <case_dir> <solution_csv> [output.png]")
        sys.exit(1)

    case_dir = sys.argv[1]
    solution_csv = sys.argv[2]
    out_path = sys.argv[3] if len(sys.argv) > 3 else 'bitmap_preview.png'
    render_png(case_dir, solution_csv, out_path)
