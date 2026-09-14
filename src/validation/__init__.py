"""Query validation layer.

Two collaborating pieces, matching the same plug-and-play spirit as the
generation providers:

- RuleBasedValidator: offline, deterministic, no network or API key.
  Handles structural checks (empty/whitespace/symbol-only/digit-only input)
  and a keyword-based STEM relevance heuristic. Always available, always
  runs first, and is the sole validator when no LLM key is configured.
- LLMQueryValidator: optional refinement that asks a language model to make
  the final "does this make sense and is it STEM" call. Only used when an
  API key is configured, and any failure (no network, bad key, timeout)
  makes it fall back to the rule-based verdict rather than blocking the
  request - see composite.py.

build_validator() is the single entry point the rest of the app should use.
"""
from __future__ import annotations

from src.config import Settings

from .base import QueryValidator
from .composite import CompositeQueryValidator
from .llm_validator import LLMQueryValidator
from .rule_based import RuleBasedValidator

__all__ = [
    "QueryValidator",
    "CompositeQueryValidator",
    "LLMQueryValidator",
    "RuleBasedValidator",
    "build_validator",
]


def build_validator(settings: Settings) -> QueryValidator:
    rule_based = RuleBasedValidator(
        min_length=settings.query_min_length,
        max_length=settings.query_max_length,
    )
    llm: LLMQueryValidator | None = None
    if settings.llm_validation_api_key:
        llm = LLMQueryValidator(
            api_key=settings.llm_validation_api_key,
            base_url=settings.llm_validation_base_url,
            model=settings.llm_validation_model,
        )
    return CompositeQueryValidator(rule_based=rule_based, llm=llm)
