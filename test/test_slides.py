"""Covers requirement 19 (slide count scales with difficulty) and the
simulated provider's content-selection logic.
"""
from __future__ import annotations

from src.domain.models import DifficultyLevel
from src.generation.simulated.slides import (
    build_beats,
    build_slides,
    fit_narration_to_budget,
    select_beats,
)


def test_slide_count_matches_difficulty_for_a_curated_topic():
    query = "How does the pH scale work?"
    counts = {d: len(build_slides(query, d)) for d in DifficultyLevel}
    assert counts[DifficultyLevel.BEGINNER] == DifficultyLevel.BEGINNER.slide_count
    assert counts[DifficultyLevel.INTERMEDIATE] == DifficultyLevel.INTERMEDIATE.slide_count
    assert counts[DifficultyLevel.BEGINNER] < counts[DifficultyLevel.INTERMEDIATE] < counts[DifficultyLevel.ADVANCED]


def test_slide_count_matches_difficulty_for_generic_fallback_topic():
    query = "How do black holes evaporate?"
    counts = {d: len(build_slides(query, d)) for d in DifficultyLevel}
    assert counts[DifficultyLevel.BEGINNER] < counts[DifficultyLevel.INTERMEDIATE] < counts[DifficultyLevel.ADVANCED]


def test_ionic_vs_covalent_gets_the_comparison_content_not_covalent_only():
    beats = build_beats("What is the difference between ionic and covalent bonding?")
    assert any("ionic" in b.title.lower() or "two ways" in b.title.lower() for b in beats)


def test_select_beats_always_keeps_first_and_last_beat():
    beats = build_beats("How does the pH scale work?")
    for n in (1, 2, 4, 6):
        chosen = select_beats(beats, n)
        assert len(chosen) == n
        assert chosen[0] == beats[0]
        assert chosen[-1] == beats[-1] or n == 1


def test_select_beats_returns_unique_slides():
    beats = build_beats("How does the pH scale work?")
    chosen = select_beats(beats, 6)
    assert len(chosen) == len(set(id(b) for b in chosen))


def test_fit_narration_to_budget_prefers_sentence_boundaries():
    text = "First sentence here. Second sentence here. Third sentence here."
    trimmed = fit_narration_to_budget(text, max_words=5)
    assert trimmed == "First sentence here."


def test_fit_narration_to_budget_noop_when_under_budget():
    text = "Short narration."
    assert fit_narration_to_budget(text, max_words=50) == text
