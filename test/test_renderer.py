from __future__ import annotations

from PIL import Image

from src.domain.models import DifficultyLevel
from src.generation.simulated.renderer import render_slide
from src.generation.simulated.slides import build_slides


def test_render_slide_produces_a_png_at_the_requested_size(tmp_path):
    slides = build_slides("How does the pH scale work?", DifficultyLevel.BEGINNER)
    out = tmp_path / "slide.png"
    render_slide(
        slides[0],
        total_slides=len(slides),
        difficulty_label="beginner",
        topic="pH scale",
        width=320,
        height=180,
        output_path=out,
    )
    assert out.exists()
    with Image.open(out) as img:
        assert img.size == (320, 180)
