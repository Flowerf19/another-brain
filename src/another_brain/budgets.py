"""Tokenizer budget validator (TASK-029).

One validator for every token budget in the locked product contract
(``config.BUDGET_*``), counting with the real Harrier tokenizer:

- topic: at most 12 tokens, humanized (``-`` → space), **without** specials;
- final document payload (``topic + "\\n" + summary.strip()``): at most 256
  tokens, **with** specials;
- final prompted query (``QUERY_PROMPT + query.strip()``): at most 128
  tokens, **with** specials;
- lexical-only content: at most 1024 tokens, **without** specials.

Rejection is explicit and never silent: over-limit input raises
:class:`ValidationError` listing the actual and allowed counts — there is no
truncation and no chunking, ever (legacy ``CONTENT_MAX_CHARS`` character
capping is gone).
"""
from __future__ import annotations

from tokenizers import Tokenizer

from another_brain.config import (
    BUDGET_CONTENT_TOKENS,
    BUDGET_DOCUMENT_TOKENS,
    BUDGET_QUERY_TOKENS,
    BUDGET_TOPIC_TOKENS,
)
from another_brain.errors import ValidationError
from another_brain.payloads import document_payload, query_payload


class TokenBudgetValidator:
    """Counts with the loaded Harrier tokenizer; pure, no model session."""

    def __init__(self, tokenizer: Tokenizer) -> None:
        self._tokenizer = tokenizer

    # -- counters -------------------------------------------------------------

    def topic_tokens(self, topic: str) -> int:
        """Humanized topic, no special tokens."""
        return len(
            self._tokenizer.encode(topic.replace("-", " "), add_special_tokens=False).ids
        )

    def document_tokens(self, topic: str, summary: str) -> int:
        """Final document payload, with special tokens."""
        return len(self._tokenizer.encode(document_payload(topic, summary)).ids)

    def query_tokens(self, query: str) -> int:
        """Final prompted query, with special tokens."""
        return len(self._tokenizer.encode(query_payload(query)).ids)

    def content_tokens(self, content: str) -> int:
        """Lexical-only content, no special tokens."""
        return len(self._tokenizer.encode(content, add_special_tokens=False).ids)

    # -- validation -----------------------------------------------------------

    def validate_remember(self, *, topic: str, summary: str, content: str) -> None:
        """Reject over-budget topic/summary-payload/content, all-at-once."""
        errors: list[str] = []
        topic_count = self.topic_tokens(topic)
        if topic_count > BUDGET_TOPIC_TOKENS:
            errors.append(
                f"topic uses {topic_count} tokens, allowed {BUDGET_TOPIC_TOKENS}"
            )
        document_count = self.document_tokens(topic, summary)
        if document_count > BUDGET_DOCUMENT_TOKENS:
            errors.append(
                f"document uses {document_count} tokens, allowed {BUDGET_DOCUMENT_TOKENS}"
            )
        content_count = self.content_tokens(content)
        if content_count > BUDGET_CONTENT_TOKENS:
            errors.append(
                f"content uses {content_count} tokens, allowed {BUDGET_CONTENT_TOKENS}"
            )
        if errors:
            raise ValidationError("; ".join(errors))

    def validate_query(self, query: str) -> None:
        """Reject empty queries and over-budget prompted queries."""
        if not query.strip():
            raise ValidationError("query must not be empty")
        count = self.query_tokens(query)
        if count > BUDGET_QUERY_TOKENS:
            raise ValidationError(
                f"query uses {count} tokens, allowed {BUDGET_QUERY_TOKENS}"
            )
