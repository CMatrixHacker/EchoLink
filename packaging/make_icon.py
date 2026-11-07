#!/usr/bin/env python
"""生成应用图标 packaging/echolink.ico(多尺寸)与预览 PNG。

    python packaging/make_icon.py
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_ICO = os.path.join(HERE, "echolink.ico")
OUT_PNG = os.path.join(HERE, "echolink-icon.png")

CYAN = (34, 229, 224)
INDIGO = (99, 102, 241)
VIOLET = (168, 85, 247)


def build(size: int) -> Image.Image:
    scale = size / 256.0
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = int(15 * scale)
    radius = int(60 * scale)
    box = (margin, margin, size - margin, size - margin)

    # 对角渐变:青 → 靛 → 紫
    gradient = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad_draw = ImageDraw.Draw(gradient)
    for y in range(size):
        t = (y + 0.0) / size
        if t < 0.55:
            k = t / 0.55
            color = tuple(round(CYAN[i] + (INDIGO[i] - CYAN[i]) * k) for i in range(3))
        else:
            k = (t - 0.55) / 0.45
            color = tuple(round(INDIGO[i] + (VIOLET[i] - INDIGO[i]) * k) for i in range(3))
        grad_draw.line([(0, y), (size, y)], fill=color + (255,))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    img.paste(gradient, (0, 0), mask)

    # 高光描边
    draw.rounded_rectangle(box, radius=radius, outline=(255, 255, 255, 110), width=max(1, int(2.4 * scale)))

    # 字标 E
    font_size = int(150 * scale)
    try:
        from PIL import ImageFont

        font = ImageFont.truetype("segoeuib.ttf", font_size)
    except OSError:
        from PIL import ImageFont

        font = ImageFont.load_default()
    text = "E"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pos = ((size - tw) // 2 - bbox[0], (size - th) // 2 - bbox[1])
    # 双层描出立体感
    draw.text((pos[0] - 1, pos[1] - 1), text, font=font, fill=(255, 255, 255, 80))
    draw.text(pos, text, font=font, fill=(4, 14, 24, 235))
    return img


def main() -> None:
    master = build(256)
    master.save(OUT_PNG)
    master.save(OUT_ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("written:", OUT_ICO, os.path.getsize(OUT_ICO), "bytes")
    print("written:", OUT_PNG)


if __name__ == "__main__":
    main()
