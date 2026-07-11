"""Unit tests for RedisKeyBuilder (Step 04 section 2)."""
import pytest

from errors import ValidationError
from storage.redis_keys import RedisKeyBuilder


@pytest.fixture
def keys():
    return RedisKeyBuilder()


class TestKeyFormats:
    def test_memory_key(self, keys):
        assert keys.memory_key("flowerf-main", "abc-123") == "ab:memory:flowerf-main:abc-123"

    def test_audit_key(self, keys):
        assert keys.audit_key("flowerf-main", "2026-07-11") == "ab:audit:flowerf-main:2026-07-11"

    def test_index_and_meta_names(self, keys):
        assert keys.index_name == "ab:idx:memory"
        assert keys.meta_key == "ab:idx:meta"

    def test_memory_prefix_cannot_match_audit_or_meta(self, keys):
        # This is the PREFIX handed to FT.CREATE — the reason the type
        # segment comes before brain_id (Step 04, decision 2).
        assert keys.memory_prefix == "ab:memory:"
        assert not keys.audit_key("b1", "2026-07-11").startswith(keys.memory_prefix)
        assert not keys.meta_key.startswith(keys.memory_prefix)

    def test_custom_prefix(self):
        keys = RedisKeyBuilder(prefix="test")
        assert keys.memory_key("b1", "m1") == "test:memory:b1:m1"
        assert keys.index_name == "test:idx:memory"


class TestParseMemoryKey:
    def test_roundtrip(self, keys):
        key = keys.memory_key("flowerf-main", "abc-123")
        assert keys.parse_memory_key(key) == ("flowerf-main", "abc-123")

    @pytest.mark.parametrize("bad", [
        "ab:audit:b1:2026-07-11",   # wrong family
        "xx:memory:b1:m1",          # wrong prefix
        "ab:memory:b1",             # missing memory_id
        "ab:memory:b1:m1:extra",    # too many segments
        "ab:memory::m1",            # empty brain_id
    ])
    def test_malformed_keys_rejected(self, keys, bad):
        with pytest.raises(ValidationError):
            keys.parse_memory_key(bad)


class TestSegmentValidation:
    def test_brain_id_with_colon_rejected(self, keys):
        with pytest.raises(ValidationError):
            keys.memory_key("bad:brain", "m1")

    def test_empty_segments_rejected(self, keys):
        with pytest.raises(ValidationError):
            keys.memory_key("", "m1")
        with pytest.raises(ValidationError):
            keys.memory_key("b1", "")

    def test_prefix_with_colon_rejected(self):
        with pytest.raises(ValidationError):
            RedisKeyBuilder(prefix="a:b")

    def test_bad_audit_day_rejected(self, keys):
        with pytest.raises(ValidationError):
            keys.audit_key("b1", "11-07-2026")
