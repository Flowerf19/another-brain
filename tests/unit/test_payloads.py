"""TASK-027: versioned payload builder + profile gate."""
import pytest

from another_brain.errors import ValidationError
from another_brain.model_manifest import MODEL_MANIFEST, QUERY_PROMPT
from another_brain.services.embedding.payloads import (
    document_payload,
    query_payload,
    validate_profile,
)


class TestDocumentPayload:
    def test_byte_exact_template(self):
        assert document_payload("my-topic", "  summary text  ") == "my topic\nsummary text"
        assert document_payload("topic", "") == "topic\n"
        assert document_payload("my-topic", "line1\nline2") == "my topic\nline1\nline2"

    def test_dashes_humanized_only_in_topic(self):
        assert document_payload("a-b-c", "keep-dashes") == "a b c\nkeep-dashes"

    def test_matches_manifest_document_template(self):
        # the manifest locks the template; the builder must implement it exactly
        assert document_payload("x", "y") == (
            "x" + "\n" + "y"
        )


class TestQueryPayload:
    def test_byte_exact_prompt_plus_strip(self):
        assert query_payload("  hello world  ") == QUERY_PROMPT + "hello world"
        assert query_payload("hello") == QUERY_PROMPT + "hello"

    def test_empty_and_whitespace_rejected(self):
        for bad in ("", "   ", "\n\t"):
            with pytest.raises(ValidationError, match="must not be empty"):
                query_payload(bad)

    def test_prompt_is_locked_manifest_constant(self):
        assert QUERY_PROMPT == (
            "Instruct: Given a web search query, retrieve relevant passages"
            " that answer the query\nQuery: "
        )
        assert query_payload("q") == MODEL_MANIFEST.query_prompt + "q"


class TestValidateProfile:
    def test_locked_profile_and_version_accepted(self):
        validate_profile(
            profile=MODEL_MANIFEST.profile, input_version=MODEL_MANIFEST.input_version
        )

    @pytest.mark.parametrize("profile", ["fp32", "bert", ""])
    def test_foreign_profile_rejected(self, profile):
        with pytest.raises(ValidationError, match="re-embed"):
            validate_profile(profile=profile, input_version=MODEL_MANIFEST.input_version)

    @pytest.mark.parametrize("input_version", [1, 3])
    def test_foreign_input_version_rejected(self, input_version):
        with pytest.raises(ValidationError, match="re-embed"):
            validate_profile(
                profile=MODEL_MANIFEST.profile, input_version=input_version
            )

    def test_error_carries_locked_values(self):
        with pytest.raises(ValidationError) as exc:
            validate_profile(profile="old", input_version=MODEL_MANIFEST.input_version)
        message = str(exc.value)
        assert "old" in message and MODEL_MANIFEST.profile in message

        with pytest.raises(ValidationError) as exc:
            validate_profile(profile=MODEL_MANIFEST.profile, input_version=1)
        message = str(exc.value)
        assert "1" in message and str(MODEL_MANIFEST.input_version) in message
