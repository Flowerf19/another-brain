"""Unit tests for the Redis codec/mapper layer (Step 04 section 1.7)."""
import pytest

from errors import ValidationError
from memory.models import EmbeddingVector, MemoryRecord
from storage.redis_keys import RedisKeyBuilder
from storage.redis_repository import (
    RedisMemoryMapper,
    _parse_search_reply,
    decode_fields,
    escape_tag_value,
    pack_embedding,
    unpack_embedding,
)

TZ = "Asia/Ho_Chi_Minh"


def make_record(**overrides):
    kwargs = dict(
        brain_id="flowerf-main",
        agent_id="agent-a",
        scope="user",
        scope_id="flowerf",
        topic="redis-index",
        summary="Tóm tắt: chọn PREFIX ab:memory: để audit key không bị index.",
        tz_name=TZ,
        now_ts=1_752_200_000.0,
        memory_id="mem-1",
    )
    kwargs.update(overrides)
    return MemoryRecord.new(**kwargs)


class TestEmbeddingCodec:
    def test_pack_unpack_roundtrip(self):
        values = [0.1, -0.25, 3.5, 0.0]
        unpacked = unpack_embedding(pack_embedding(values))
        assert unpacked == pytest.approx(values)

    def test_unpack_non_bytes_returns_empty(self):
        assert unpack_embedding("not-bytes") == []
        assert unpack_embedding(None) == []


class TestTagEscaping:
    def test_escapes_dashes_in_dates_and_ids(self):
        assert escape_tag_value("2026-07-11") == r"2026\-07\-11"
        assert escape_tag_value("flowerf-main") == r"flowerf\-main"

    def test_word_chars_untouched(self):
        assert escape_tag_value("abc_123") == "abc_123"


class TestDecodeFields:
    def test_resp2_flat_pair_list_with_bytes(self):
        flat = [b"topic", b"redis-index", b"importance", b"4", b"period_start", b"123.5"]
        decoded = decode_fields(flat)
        assert decoded == {"topic": "redis-index", "importance": 4, "period_start": 123.5}

    def test_embedding_unpacked_before_utf8_decode(self):
        raw = pack_embedding([1.0, 0.0])
        decoded = decode_fields({b"embedding": raw})
        assert decoded["embedding"] == pytest.approx([1.0, 0.0])


class TestMapperRoundtrip:
    def setup_method(self):
        self.keys = RedisKeyBuilder()
        self.mapper = RedisMemoryMapper(self.keys)

    def _to_wire(self, mapping):
        """Simulate Redis storage: everything becomes bytes."""
        wire = {}
        for key, value in mapping.items():
            wire[key.encode()] = value if isinstance(value, bytes) else str(value).encode()
        return wire

    def test_live_record_has_no_deleted_at_field(self):
        record = make_record()
        mapping = self.mapper.record_to_hash(record, EmbeddingVector.from_list([1.0, 0.0], 2))
        assert "deleted_at" not in mapping
        assert len(mapping) == 17  # 18 contract fields minus deleted_at (absent when live)

    def test_roundtrip_through_wire_format(self):
        record = make_record(
            content="- [x] fixed\n- [ ] verify",
            importance=5,
            metadata={"origin": "discord", "msg": 12},
            catalog="bug",
        )
        mapping = self.mapper.record_to_hash(record, EmbeddingVector.from_list([1.0, 0.0], 2))
        fields = decode_fields(self._to_wire(mapping))
        fields.pop("embedding")
        key = self.keys.memory_key("flowerf-main", "mem-1")
        loaded = self.mapper.hash_to_record(key, fields)
        assert loaded == record

    def test_deleted_record_keeps_deleted_at(self):
        from dataclasses import replace
        record = replace(make_record(), deleted_at=1_752_200_100.0)
        mapping = self.mapper.record_to_hash(record, EmbeddingVector.from_list([1.0, 0.0], 2))
        assert mapping["deleted_at"] == 1_752_200_100.0

    def test_missing_required_field_raises(self):
        key = self.keys.memory_key("b1", "m1")
        with pytest.raises(ValidationError, match="missing field"):
            self.mapper.hash_to_record(key, {"topic": "x"})


class TestParseSearchReply:
    def test_resp2_without_scores(self):
        reply = [1, b"ab:memory:b1:m1", [b"topic", b"redis-index", b"score", b"0.25"]]
        parsed = _parse_search_reply(reply, has_scores=False)
        assert len(parsed) == 1
        key, score, fields = parsed[0]
        assert key == "ab:memory:b1:m1"
        assert score is None
        assert fields["score"] == 0.25  # KNN AS-score arrives as a field

    def test_resp2_with_scores(self):
        reply = [1, b"ab:memory:b1:m1", b"1.5", [b"topic", b"redis-index"]]
        parsed = _parse_search_reply(reply, has_scores=True)
        key, score, fields = parsed[0]
        assert score == 1.5
        assert fields == {"topic": "redis-index"}

    def test_resp3_dict_shape(self):
        reply = {
            "results": [
                {"id": "ab:memory:b1:m1", "extra_attributes": {"topic": "redis-index"}},
            ]
        }
        parsed = _parse_search_reply(reply, has_scores=False)
        assert parsed[0][0] == "ab:memory:b1:m1"
        assert parsed[0][2] == {"topic": "redis-index"}
