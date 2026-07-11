"""Unit tests for ModelInstallPolicy (Step 03)."""
import pytest

from errors import ConfigError
from models.policy import (
    TRIGGER_EXPLICIT,
    TRIGGER_FIRST_USE,
    TRIGGER_STARTUP,
    ModelInstallPolicy,
)


class TestParse:
    @pytest.mark.parametrize("raw,expected", [
        ("disabled", ModelInstallPolicy.DISABLED),
        ("manual", ModelInstallPolicy.MANUAL),
        ("lazy", ModelInstallPolicy.LAZY),
        ("on_start", ModelInstallPolicy.ON_START),
        ("MANUAL", ModelInstallPolicy.MANUAL),
        ("  lazy  ", ModelInstallPolicy.LAZY),
        ("On_Start", ModelInstallPolicy.ON_START),
    ])
    def test_valid_values_parsed(self, raw, expected):
        assert ModelInstallPolicy.parse(raw) is expected

    def test_invalid_value_lists_options(self):
        with pytest.raises(ConfigError) as exc:
            ModelInstallPolicy.parse("yolo")
        message = str(exc.value)
        assert "disabled" in message
        assert "manual" in message
        assert "lazy" in message
        assert "on_start" in message
        assert "yolo" in message


class TestMayDownload:
    @pytest.mark.parametrize("policy,trigger,expected", [
        (ModelInstallPolicy.DISABLED, TRIGGER_EXPLICIT, False),
        (ModelInstallPolicy.DISABLED, TRIGGER_STARTUP, False),
        (ModelInstallPolicy.DISABLED, TRIGGER_FIRST_USE, False),
        (ModelInstallPolicy.MANUAL, TRIGGER_EXPLICIT, True),
        (ModelInstallPolicy.MANUAL, TRIGGER_STARTUP, False),
        (ModelInstallPolicy.MANUAL, TRIGGER_FIRST_USE, False),
        (ModelInstallPolicy.LAZY, TRIGGER_EXPLICIT, True),
        (ModelInstallPolicy.LAZY, TRIGGER_STARTUP, False),
        (ModelInstallPolicy.LAZY, TRIGGER_FIRST_USE, True),
        (ModelInstallPolicy.ON_START, TRIGGER_EXPLICIT, True),
        (ModelInstallPolicy.ON_START, TRIGGER_STARTUP, True),
        (ModelInstallPolicy.ON_START, TRIGGER_FIRST_USE, False),
    ])
    def test_matrix(self, policy, trigger, expected):
        assert policy.may_download(trigger) is expected

    @pytest.mark.parametrize("policy", list(ModelInstallPolicy))
    def test_unknown_trigger_rejected(self, policy):
        with pytest.raises(ValueError):
            policy.may_download("on_full_moon")
