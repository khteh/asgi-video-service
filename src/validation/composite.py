"""Composes the rule-based and (optional) LLM validators."""
from __future__ import annotations

from typing import Optional

from src.domain.errors import NotStemRelevantError

from .llm_validator import LLMQueryValidator
from .rule_based import RuleBasedValidator


class CompositeQueryValidator:
    """Structural check always runs locally; the semantic/relevance
    verdict comes from the LLM validator when one is configured and
    reachable, otherwise from the rule-based heuristic.
    """

    def __init__(
        self,
        rule_based: RuleBasedValidator,
        llm: Optional[LLMQueryValidator] = None,
    ) -> None:
        self.rule_based = rule_based
        self.llm = llm

    async def validate(self, raw_query: str) -> str:
        query = self.rule_based.structural_check(raw_query)

        if self.llm is not None:
            outcome = await self.llm.check(query)
            if outcome is not None:
                if not outcome["makes_sense"]:
                    raise NotStemRelevantError(
                        outcome["reason"] or "Query doesn't make semantic sense.",
                        code="not_semantic",
                    )
                if not outcome["is_stem"]:
                    raise NotStemRelevantError(
                        outcome["reason"] or "Query isn't about a STEM topic."
                    )
                return query
            # LLM unreachable/unusable -> fall through to rule-based verdict.

        self.rule_based.relevance_check(query)
        return query
