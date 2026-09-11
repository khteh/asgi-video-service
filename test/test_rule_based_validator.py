"""Covers the spec's explicit failure cases (1) and (2), plus the happy
path for the three example STEM questions.
"""
from __future__ import annotations

import pytest

from src.domain.errors import InvalidQueryError, NotStemRelevantError
from src.validation.rule_based import RuleBasedValidator

EXAMPLE_QUERIES = [
    "How does the pH scale work?",
    "Why do atoms form covalent bonds?",
    "What is the difference between ionic and covalent bonding?",
]


@pytest.fixture
def validator():
    return RuleBasedValidator()


@pytest.mark.parametrize("raw_query", ["", "   ", "~!@#$", "12345", "     "])
def test_structurally_invalid_queries_are_rejected(validator, raw_query):
    with pytest.raises(InvalidQueryError):
        validator.structural_check(raw_query)


@pytest.mark.parametrize("query", EXAMPLE_QUERIES)
def test_example_stem_queries_pass_both_checks(validator, query):
    cleaned = validator.structural_check(query)
    validator.relevance_check(cleaned)  # should not raise


def test_gibberish_is_rejected_as_not_semantic(validator):
    query = validator.structural_check("asdkfj qwoeiur zxcvbnmqq plkjhgfd")
    with pytest.raises(NotStemRelevantError) as excinfo:
        validator.relevance_check(query)
    assert excinfo.value.code == "not_semantic"


def test_off_topic_but_real_sentence_is_rejected(validator):
    query = validator.structural_check("What is your favorite pizza topping?")
    with pytest.raises(NotStemRelevantError) as excinfo:
        validator.relevance_check(query)
    assert excinfo.value.code == "not_stem_relevant"


@pytest.mark.asyncio
@pytest.mark.parametrize("query", EXAMPLE_QUERIES)
async def test_validate_end_to_end_returns_cleaned_query(validator, query):
    result = await validator.validate(query)
    assert result == query


@pytest.mark.asyncio
async def test_validate_raises_before_any_side_effect_on_bad_input(validator):
    with pytest.raises(InvalidQueryError):
        await validator.validate("     ")
    with pytest.raises(NotStemRelevantError):
        await validator.validate("What is your favorite pizza topping?")
