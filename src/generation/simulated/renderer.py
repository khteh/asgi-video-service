"""Renders a Slide into a still PNG frame using Pillow.

Fonts are bundled under ./assets/fonts (DejaVu Sans, Bitstream Vera
license - see assets/fonts/LICENSE_DEJAVU.txt) so rendering looks the same
regardless of what fonts happen to be installed on the host.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .slides import Slide

_ASSETS_DIR = Path(__file__).parent / "assets"
_FONT_REGULAR = _ASSETS_DIR / "fonts" / "DejaVuSans.ttf"
_FONT_BOLD = _ASSETS_DIR / "fonts" / "DejaVuSans-Bold.ttf"

# A small rotating palette so different topics feel visually distinct
# without any external image assets. Picked for reasonable contrast in a
# dark-themed slide (light text/bullets on a deep background).
_PALETTES = [
    {"bg_top": (14, 22, 38), "bg_bottom": (23, 38, 64), "accent": (94, 189, 255)},
    {"bg_top": (20, 16, 38), "bg_bottom": (44, 27, 74), "accent": (196, 141, 255)},
    {"bg_top": (10, 28, 24), "bg_bottom": (18, 51, 42), "accent": (99, 230, 190)},
    {"bg_top": (32, 18, 14), "bg_bottom": (61, 32, 20), "accent": (255, 173, 94)},
    {"bg_top": (28, 14, 22), "bg_bottom": (56, 22, 40), "accent": (255, 120, 150)},
]


def _palette_for(topic: str) -> dict:
    digest = hashlib.sha256(topic.encode("utf-8")).digest()
    return _PALETTES[digest[0] % len(_PALETTES)]


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _vertical_gradient(width: int, height: int, top: tuple, bottom: tuple) -> Image.Image:
    base = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(base)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(int(top[c] + (bottom[c] - top[c]) * t) for c in range(3))
        draw.line([(0, y), (width, y)], fill=color)
    return base


def _draw_decoration(image: Image.Image, accent: tuple, width: int, height: int, seed: int) -> None:
    """A large, soft concentric-ring motif in the lower-right area, purely
    decorative, so a slide reads as a designed graphic rather than a bare
    text card. Drawn on an RGBA overlay so it can be semi-transparent.
    """
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    cx = int(width * 0.86)
    cy = int(height * 0.62)
    base_radius = int(height * 0.42)
    ring_count = 5
    for i in range(ring_count, 0, -1):
        r = int(base_radius * i / ring_count)
        alpha = int(14 + (ring_count - i) * 5)
        odraw.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            outline=(*accent, alpha + 30),
            width=max(2, int(height * 0.003)),
        )
    # A few small satellite dots for texture, deterministic per-slide.
    import math

    for i in range(6):
        angle = (seed * 47 + i * 61) % 360
        rad = math.radians(angle)
        dist = base_radius * (0.35 + 0.5 * ((seed + i * 17) % 100) / 100)
        dx = int(cx + dist * math.cos(rad))
        dy = int(cy + dist * math.sin(rad))
        dot_r = max(2, int(height * 0.006))
        odraw.ellipse([dx - dot_r, dy - dot_r, dx + dot_r, dy + dot_r], fill=(*accent, 70))

    image.paste(Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB"), (0, 0))


def render_slide(
    slide: Slide,
    *,
    total_slides: int,
    difficulty_label: str,
    topic: str,
    width: int,
    height: int,
    output_path: Path,
) -> None:
    palette = _palette_for(topic)
    image = _vertical_gradient(width, height, palette["bg_top"], palette["bg_bottom"])
    _draw_decoration(image, palette["accent"], width, height, seed=slide.index + 1)
    draw = ImageDraw.Draw(image)

    margin = int(width * 0.08)
    accent = palette["accent"]

    # Accent bar + eyebrow label.
    bar_height = int(height * 0.012)
    draw.rectangle([0, 0, width, bar_height], fill=accent)

    eyebrow_font = _font(_FONT_BOLD, int(height * 0.028))
    draw.text(
        (margin, int(height * 0.06)),
        "STEM EXPLAINER",
        font=eyebrow_font,
        fill=accent,
    )

    # Title (wrapped).
    title_font = _font(_FONT_BOLD, int(height * 0.075))
    title_lines = _wrap_text(draw, slide.title, title_font, width - 2 * margin)
    title_y = int(height * 0.14)
    line_height = int(height * 0.09)
    for line in title_lines[:3]:
        draw.text((margin, title_y), line, font=title_font, fill=(255, 255, 255))
        title_y += line_height

    # Bullets.
    bullet_font = _font(_FONT_REGULAR, int(height * 0.038))
    bullet_y = title_y + int(height * 0.03)
    bullet_indent = margin + int(width * 0.02)
    bullet_text_width = width - bullet_indent - margin
    for bullet in slide.bullets[:5]:
        lines = _wrap_text(draw, bullet, bullet_font, bullet_text_width)
        dot_radius = int(height * 0.007)
        dot_y = bullet_y + int(height * 0.017)
        draw.ellipse(
            [margin, dot_y - dot_radius, margin + 2 * dot_radius, dot_y + dot_radius],
            fill=accent,
        )
        for i, line in enumerate(lines):
            draw.text(
                (bullet_indent, bullet_y + i * int(height * 0.05)),
                line,
                font=bullet_font,
                fill=(225, 230, 240),
            )
        bullet_y += max(1, len(lines)) * int(height * 0.05) + int(height * 0.02)

    # Footer: slide progress + difficulty badge.
    footer_font = _font(_FONT_REGULAR, int(height * 0.026))
    footer_y = height - int(height * 0.07)
    draw.text(
        (margin, footer_y),
        f"Slide {slide.index + 1} of {total_slides}  ·  {difficulty_label.title()} level",
        font=footer_font,
        fill=(170, 178, 196),
    )

    # Progress dots.
    dot_count = total_slides
    dot_radius = max(3, int(height * 0.006))
    dot_gap = int(width * 0.02)
    total_dots_width = dot_count * (2 * dot_radius) + (dot_count - 1) * dot_gap
    start_x = width - margin - total_dots_width
    for i in range(dot_count):
        cx = start_x + i * (2 * dot_radius + dot_gap) + dot_radius
        cy = footer_y + int(height * 0.012)
        color = accent if i <= slide.index else (90, 96, 112)
        draw.ellipse(
            [cx - dot_radius, cy - dot_radius, cx + dot_radius, cy + dot_radius],
            fill=color,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, "PNG")
