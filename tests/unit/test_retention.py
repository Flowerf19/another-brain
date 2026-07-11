"""Unit tests for RetentionPolicy (Step 04 section 4)."""
import pytest

from errors import ConfigError, ValidationError
from memory.retention import DEFAULT_TTL_BY_IMPORTANCE, RetentionPolicy


class TestDefaultTable:
    def test_march7_proven_values(self):
        policy = RetentionPolicy()
        assert policy.ttl_seconds(5) == 365 * 86400
        assert policy.ttl_seconds(4) == 180 * 86400
        assert policy.ttl_seconds(3) == 90 * 86400
        assert policy.ttl_seconds(2) == 30 * 86400
        assert policy.ttl_seconds(1) == 7 * 86400

    def test_default_table_covers_exactly_1_to_5(self):
        assert set(DEFAULT_TTL_BY_IMPORTANCE) == {1, 2, 3, 4, 5}


class TestValidation:
    @pytest.mark.parametrize("importance", [0, 6, True, "3"])
    def test_invalid_importance_rejected(self, importance):
        with pytest.raises(ValidationError):
            RetentionPolicy().ttl_seconds(importance)

    def test_missing_importance_level_rejected(self):
        table = dict(DEFAULT_TTL_BY_IMPORTANCE)
        del table[2]
        with pytest.raises(ConfigError):
            RetentionPolicy(table)

    def test_non_positive_ttl_rejected(self):
        table = dict(DEFAULT_TTL_BY_IMPORTANCE)
        table[1] = 0
        with pytest.raises(ConfigError):
            RetentionPolicy(table)

    def test_custom_table_used(self):
        table = {1: 10, 2: 20, 3: 30, 4: 40, 5: 50}
        policy = RetentionPolicy(table)
        assert policy.ttl_seconds(3) == 30
