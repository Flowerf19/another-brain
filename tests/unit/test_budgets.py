"""TASK-029: token budget validator — every boundary ±1, VI/EN input,
query/document asymmetry, no truncation, all-at-once rejection.

Uses a deterministic WordLevel tokenizer with a known vocab and a
[CLS]/[SEP] post-processor, so specials counts are exact and controllable."""
from __future__ import annotations

import pytest
from tokenizers import Tokenizer, models, pre_tokenizers, processors

from another_brain.budgets import TokenBudgetValidator
from another_brain.config import (
    BUDGET_CONTENT_TOKENS,
    BUDGET_DOCUMENT_TOKENS,
    BUDGET_QUERY_TOKENS,
    BUDGET_TOPIC_TOKENS,
)
from another_brain.errors import ValidationError
from another_brain.model_manifest import QUERY_PROMPT

VOCAB = {
    "[CLS]": 0, "[SEP]": 1, "[UNK]": 2,
    "my": 3, "topic": 4, "hello": 5, "world": 6, "word": 7,
    "Instruct:": 8, "Given": 9, "a": 10, "web": 11, "search": 12,
    "query,": 13, "retrieve": 14, "relevant": 15, "passages": 16,
    "that": 17, "answer": 18, "the": 19, "query": 20, "Query:": 21,
}


@pytest.fixture
def validator() -> TokenBudgetValidator:
    tokenizer = Tokenizer(models.WordLevel(vocab=VOCAB, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]",
        special_tokens=[("[CLS]", 0), ("[SEP]", 1)],
    )
    return TokenBudgetValidator(tokenizer)


class TestCounters:
    def test_topic_counts_humanized_without_specials(self, validator):
        assert validator.topic_tokens("my-topic") == 2  # "my topic"
        assert validator.topic_tokens("my topic") == 2

    def test_document_counts_with_specials(self, validator):
        # "my topic\nhello world" → 4 words + [CLS]/[SEP] = 6
        assert validator.document_tokens("my-topic", "hello world") == 6

    def test_query_asymmetric_with_prompt_and_specials(self, validator):
        # measured with the toy fixture: QUERY_PROMPT+specials = 19 tokens
        # (the real Harrier prompt is 19–21, per the corpus note); "hello world"
        # adds 2 → 21. Document payload has no prompt and no such asymmetry.
        assert validator.query_tokens("hello world") == 21
        assert validator.query_tokens("hello world") != validator.document_tokens("x", "hello world")

    def test_content_counts_without_specials(self, validator):
        assert validator.content_tokens("hello world hello world") == 4


class TestTopicBudget:
    def test_limit_passes(self, validator):
        topic = " ".join(["word"] * BUDGET_TOPIC_TOKENS)
        assert validator.topic_tokens(topic) == BUDGET_TOPIC_TOKENS
        validator.validate_remember(topic=topic, summary="s", content="c")

    def test_limit_plus_one_rejected_with_counts(self, validator):
        topic = " ".join(["word"] * (BUDGET_TOPIC_TOKENS + 1))
        with pytest.raises(ValidationError, match=(
            f"topic uses {BUDGET_TOPIC_TOKENS + 1} tokens, allowed {BUDGET_TOPIC_TOKENS}"
        )):
            validator.validate_remember(topic=topic, summary="s", content="c")


class TestDocumentBudget:
    def _document_at(self, validator, word_count: int) -> tuple[str, str]:
        """topic with 1 word + summary padded to word_count total document words."""
        return "topic", " ".join(["word"] * (word_count - 1))

    def test_limit_passes(self, validator):
        topic, summary = self._document_at(validator, BUDGET_DOCUMENT_TOKENS - 2)
        assert validator.document_tokens(topic, summary) == BUDGET_DOCUMENT_TOKENS
        validator.validate_remember(topic=topic, summary=summary, content="c")

    def test_limit_plus_one_rejected(self, validator):
        topic, summary = self._document_at(validator, BUDGET_DOCUMENT_TOKENS - 1)
        assert validator.document_tokens(topic, summary) == BUDGET_DOCUMENT_TOKENS + 1
        with pytest.raises(ValidationError, match=(
            f"document uses {BUDGET_DOCUMENT_TOKENS + 1} tokens, allowed {BUDGET_DOCUMENT_TOKENS}"
        )):
            validator.validate_remember(topic=topic, summary=summary, content="c")


class TestQueryBudget:
    # toy fixture: QUERY_PROMPT + specials = 19 tokens (measured); budget 128
    # → 109 query words = exactly 128, 110 words = 129 (limit+1).

    def test_limit_passes(self, validator):
        query = " ".join(["word"] * (BUDGET_QUERY_TOKENS - 19))
        assert validator.query_tokens(query) == BUDGET_QUERY_TOKENS
        validator.validate_query(query)

    def test_limit_plus_one_rejected(self, validator):
        query = " ".join(["word"] * (BUDGET_QUERY_TOKENS - 18))
        with pytest.raises(ValidationError, match=(
            f"query uses {BUDGET_QUERY_TOKENS + 1} tokens, allowed {BUDGET_QUERY_TOKENS}"
        )):
            validator.validate_query(query)

    def test_empty_query_rejected(self, validator):
        with pytest.raises(ValidationError, match="must not be empty"):
            validator.validate_query("   ")


class TestContentBudget:
    def test_limit_passes(self, validator):
        content = " ".join(["word"] * BUDGET_CONTENT_TOKENS)
        assert validator.content_tokens(content) == BUDGET_CONTENT_TOKENS
        validator.validate_remember(topic="t", summary="s", content=content)

    def test_limit_plus_one_rejected(self, validator):
        content = " ".join(["word"] * (BUDGET_CONTENT_TOKENS + 1))
        with pytest.raises(ValidationError, match=(
            f"content uses {BUDGET_CONTENT_TOKENS + 1} tokens, allowed {BUDGET_CONTENT_TOKENS}"
        )):
            validator.validate_remember(topic="t", summary="s", content=content)


class TestNoTruncation:
    def test_oversized_content_counted_fully_not_truncated(self, validator):
        content = " ".join(["word"] * 5000)
        assert validator.content_tokens(content) == 5000  # full count, never cut
        with pytest.raises(ValidationError):
            validator.validate_remember(topic="t", summary="s", content=content)

    def test_no_character_capping(self, validator):
        # legacy CONTENT_MAX_CHARS (4000 chars) is gone: token count is what matters
        long_chars = ("a" * 50 + " ") * 90  # ~4590 chars but vocab → unknown per word
        assert validator.content_tokens(long_chars) == 90
        validator.validate_remember(topic="t", summary="s", content=long_chars)


class TestAllAtOnce:
    def test_multiple_overbudget_fields_reported_together(self, validator):
        topic = " ".join(["word"] * (BUDGET_TOPIC_TOKENS + 1))
        content = " ".join(["word"] * (BUDGET_CONTENT_TOKENS + 1))
        with pytest.raises(ValidationError) as exc:
            validator.validate_remember(topic=topic, summary="s", content=content)
        message = str(exc.value)
        assert f"topic uses {BUDGET_TOPIC_TOKENS + 1} tokens" in message
        assert f"content uses {BUDGET_CONTENT_TOKENS + 1} tokens" in message
        assert "document uses" not in message  # document within budget

    def test_prompt_itself_fits_query_budget(self, validator):
        # QUERY_PROMPT + 2 specials is far under 128 → plain queries pass
        assert validator.query_tokens("hello world") <= BUDGET_QUERY_TOKENS
