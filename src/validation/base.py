"""Validator interface."""
from __future__ import annotations

from typing import Protocol


class QueryValidator(Protocol):
    async def validate(self, raw_query: str) -> str:
        """Validate and normalize a raw learner query.

        Returns the cleaned query string on success.

        Raises:
            InvalidQueryError: structurally invalid input (empty,
                whitespace-only, symbol/digit noise, too short/long).
            NotStemRelevantError: well-formed text that either doesn't make
                semantic sense or isn't about a STEM topic.

        Implementations must never create side effects (no job, no
        persistence) - this runs strictly before a Job exists.
        """
        ...
