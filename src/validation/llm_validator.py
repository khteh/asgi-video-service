"""Optional LLM-backed semantic validator.

This is a *refinement* on top of RuleBasedValidator, not a replacement: the
rule-based layer always runs its structural check first, and this class is
only consulted for the semantic/relevance judgment, and only when an API
key is configured. If the LLM is unreachable, times out, or returns
something unusable, `check()` returns None and the caller (see
composite.py) falls back to the rule-based relevance heuristic - an
optional accuracy improvement must never turn into a hard dependency for
submitting a job.
"""
from __future__ import annotations

import json
import logging
from typing import Optional, TypedDict

import httpx

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You judge whether a short, learner-submitted question is (a) coherent, "
    "semantically meaningful text - as opposed to gibberish, keyboard "
    "mashing, or a meaningless word salad - and (b) about a Science, "
    "Technology, Engineering, or Mathematics (STEM) topic. "
    "Respond with ONLY a compact JSON object of the exact shape "
    '{"makes_sense": boolean, "is_stem": boolean, "reason": string}. '
    "The reason should be one short sentence. No prose outside the JSON."
)


class LLMValidationOutcome(TypedDict):
    makes_sense: bool
    is_stem: bool
    reason: str


class LLMQueryValidator:
    """Generic Anthropic-Messages-API-shaped validator.

    Vendor-agnostic in spirit: swap `base_url`/`model`/headers to point this
    at any chat-completion-style API that can follow a JSON-only
    instruction, without touching the rest of the validation layer.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.anthropic.com/v1/messages",
        model: str = "claude-3-5-haiku-latest",
        timeout_seconds: float = 8.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def check(self, query: str) -> Optional[LLMValidationOutcome]:
        payload = {
            "model": self.model,
            "max_tokens": 200,
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": query}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(self.base_url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                text = "".join(
                    block.get("text", "")
                    for block in data.get("content", [])
                    if block.get("type") == "text"
                )
                parsed = json.loads(text)
                return {
                    "makes_sense": bool(parsed.get("makes_sense", True)),
                    "is_stem": bool(parsed.get("is_stem", True)),
                    "reason": str(parsed.get("reason", "")),
                }
        except Exception as exc:  # noqa: BLE001 - best-effort refinement, never fatal
            logger.warning(
                "LLM query validation unavailable, falling back to rule-based verdict: %s",
                exc,
            )
            return None
