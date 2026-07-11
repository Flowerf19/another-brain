"""Unit tests for ModelInstaller (Step 03)."""
from pathlib import Path

import pytest

import models.installer as installer_module
from errors import ConfigError
from models.cache import ModelCache
from models.installer import ModelInstaller
from models.policy import (
    TRIGGER_EXPLICIT,
    TRIGGER_FIRST_USE,
    TRIGGER_STARTUP,
    ModelInstallPolicy,
)
from models.registry import KIND_EMBEDDING, ModelSpec
from models.runtime import ModelRuntimeProfile

SPEC = ModelSpec(
    name="microsoft/harrier-oss-v1-270m",
    kind=KIND_EMBEDDING,
    expected_dim=640,
    query_prompt_name="query",
)


def make_installer(tmp_path, *, policy=ModelInstallPolicy.MANUAL, allow_network=False,
                    pinned_revision=""):
    cache = ModelCache(tmp_path / "cache")
    installer = ModelInstaller(
        cache, policy, allow_network=allow_network, pinned_revision=pinned_revision,
    )
    return installer, cache


def fake_snapshot_download(monkeypatch):
    """Record snapshot_download calls and materialize the target dir, so a
    successful pull never touches the network or huggingface_hub."""
    calls = []

    def _fake(*, repo_id, revision, local_dir):
        calls.append({"repo_id": repo_id, "revision": revision, "local_dir": local_dir})
        Path(local_dir).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(installer_module, "_import_snapshot_download", lambda: _fake)
    return calls


class TestPullSuccess:
    def test_pull_downloads_and_writes_meta(self, tmp_path, monkeypatch):
        calls = fake_snapshot_download(monkeypatch)
        installer, cache = make_installer(
            tmp_path, policy=ModelInstallPolicy.MANUAL, allow_network=True,
        )

        target = installer.pull(SPEC)

        assert target == cache.model_dir(SPEC.name)
        assert len(calls) == 1
        assert calls[0] == {
            "repo_id": SPEC.name,
            "revision": None,
            "local_dir": str(cache.model_dir(SPEC.name)),
        }
        assert cache.is_cached(SPEC.name) is True
        meta = cache.read_meta(SPEC.name)
        assert meta["provider"] == "local"
        assert meta["model_name"] == SPEC.name
        assert meta["kind"] == SPEC.kind
        assert meta["expected_dim"] == SPEC.expected_dim
        assert meta["query_prompt_name"] == SPEC.query_prompt_name
        assert meta["trigger"] == TRIGGER_EXPLICIT

    def test_second_pull_is_noop(self, tmp_path, monkeypatch):
        calls = fake_snapshot_download(monkeypatch)
        installer, cache = make_installer(
            tmp_path, policy=ModelInstallPolicy.MANUAL, allow_network=True,
        )

        installer.pull(SPEC)
        second_target = installer.pull(SPEC)

        assert len(calls) == 1
        assert second_target == cache.model_dir(SPEC.name)

    def test_pull_passes_pinned_revision(self, tmp_path, monkeypatch):
        calls = fake_snapshot_download(monkeypatch)
        installer, cache = make_installer(
            tmp_path, policy=ModelInstallPolicy.MANUAL, allow_network=True,
            pinned_revision="  v1.2  ",
        )

        installer.pull(SPEC)

        assert calls[0]["revision"] == "v1.2"
        assert cache.read_meta(SPEC.name)["revision"] == "v1.2"


class TestEnsureGates:
    def test_cached_short_circuits_without_network(self, tmp_path):
        installer, cache = make_installer(
            tmp_path, policy=ModelInstallPolicy.DISABLED, allow_network=False,
        )
        cache.write_meta(SPEC.name, {"provider": "local"})

        result = installer.ensure(SPEC, TRIGGER_EXPLICIT)

        assert result == cache.model_dir(SPEC.name)

    def test_disabled_blocks_ensure(self, tmp_path):
        installer, _cache = make_installer(
            tmp_path, policy=ModelInstallPolicy.DISABLED, allow_network=True,
        )

        with pytest.raises(ConfigError):
            installer.ensure(SPEC, TRIGGER_EXPLICIT)

    def test_disabled_blocks_explicit_pull(self, tmp_path):
        installer, _cache = make_installer(
            tmp_path, policy=ModelInstallPolicy.DISABLED, allow_network=True,
        )

        with pytest.raises(ConfigError, match="disabled"):
            installer.pull(SPEC, trigger=TRIGGER_EXPLICIT)

    @pytest.mark.parametrize("trigger", [TRIGGER_STARTUP, TRIGGER_FIRST_USE])
    def test_manual_blocks_non_explicit_triggers(self, tmp_path, trigger):
        installer, _cache = make_installer(
            tmp_path, policy=ModelInstallPolicy.MANUAL, allow_network=True,
        )

        with pytest.raises(ConfigError, match="model pull"):
            installer.ensure(SPEC, trigger)

    def test_lazy_allows_first_use(self, tmp_path, monkeypatch):
        calls = fake_snapshot_download(monkeypatch)
        installer, cache = make_installer(
            tmp_path, policy=ModelInstallPolicy.LAZY, allow_network=True,
        )

        result = installer.ensure(SPEC, TRIGGER_FIRST_USE)

        assert result == cache.model_dir(SPEC.name)
        assert len(calls) == 1

    def test_lazy_blocks_startup(self, tmp_path):
        installer, _cache = make_installer(
            tmp_path, policy=ModelInstallPolicy.LAZY, allow_network=True,
        )

        with pytest.raises(ConfigError, match="model pull"):
            installer.ensure(SPEC, TRIGGER_STARTUP)

    def test_on_start_allows_startup(self, tmp_path, monkeypatch):
        calls = fake_snapshot_download(monkeypatch)
        installer, cache = make_installer(
            tmp_path, policy=ModelInstallPolicy.ON_START, allow_network=True,
        )

        result = installer.ensure(SPEC, TRIGGER_STARTUP)

        assert result == cache.model_dir(SPEC.name)
        assert len(calls) == 1

    def test_on_start_blocks_first_use(self, tmp_path):
        installer, _cache = make_installer(
            tmp_path, policy=ModelInstallPolicy.ON_START, allow_network=True,
        )

        with pytest.raises(ConfigError, match="model pull"):
            installer.ensure(SPEC, TRIGGER_FIRST_USE)

    def test_allow_network_false_blocks_pull(self, tmp_path):
        installer, _cache = make_installer(
            tmp_path, policy=ModelInstallPolicy.MANUAL, allow_network=False,
        )

        with pytest.raises(ConfigError, match="MODEL_ALLOW_NETWORK"):
            installer.pull(SPEC, trigger=TRIGGER_EXPLICIT)

    def test_allow_network_false_blocks_ensure_for_allowed_trigger(self, tmp_path):
        installer, _cache = make_installer(
            tmp_path, policy=ModelInstallPolicy.LAZY, allow_network=False,
        )

        with pytest.raises(ConfigError, match="MODEL_ALLOW_NETWORK"):
            installer.ensure(SPEC, TRIGGER_FIRST_USE)


class TestPlan:
    def test_plan_fields_not_cached(self, tmp_path):
        installer, cache = make_installer(
            tmp_path, policy=ModelInstallPolicy.LAZY, allow_network=True,
        )

        plan = installer.plan(SPEC)

        assert plan == {
            "model": SPEC.name,
            "kind": SPEC.kind,
            "cached": False,
            "target": str(cache.model_dir(SPEC.name)),
            "revision": "latest",
            "expected_dim": SPEC.expected_dim,
            "download_policy": "lazy",
            "network_allowed": True,
        }

    def test_plan_reflects_cached_and_pinned_revision(self, tmp_path):
        installer, cache = make_installer(
            tmp_path, policy=ModelInstallPolicy.MANUAL, allow_network=False,
            pinned_revision="v9",
        )
        cache.write_meta(SPEC.name, {"provider": "local"})

        plan = installer.plan(SPEC)

        assert plan["cached"] is True
        assert plan["revision"] == "v9"


class TestStatus:
    def test_status_local_not_cached(self, tmp_path):
        installer, cache = make_installer(
            tmp_path, policy=ModelInstallPolicy.MANUAL, allow_network=False,
        )
        profile = ModelRuntimeProfile(weight_precision="fp16", device="cpu")

        status = installer.status(SPEC, provider="local", profile=profile).to_dict()

        assert status["cached"] is False
        assert status["cache_path"] == str(cache.model_dir(SPEC.name))
        assert status["weight_precision"] == "fp16"
        assert status["device"] == "cpu"
        assert status["provider"] == "local"

    def test_status_local_cached_without_profile(self, tmp_path):
        installer, cache = make_installer(
            tmp_path, policy=ModelInstallPolicy.MANUAL, allow_network=False,
        )
        cache.write_meta(SPEC.name, {"provider": "local"})

        status = installer.status(SPEC, provider="local").to_dict()

        assert status["cached"] is True
        assert status["weight_precision"] is None
        assert status["device"] is None

    def test_status_external_provider(self, tmp_path):
        installer, _cache = make_installer(
            tmp_path, policy=ModelInstallPolicy.MANUAL, allow_network=False,
        )

        status = installer.status(SPEC, provider="openai_compat").to_dict()

        assert status["cached"] is True
        assert status["cache_path"] is None
        assert status["weight_precision"] is None
        assert status["device"] is None
        assert status["provider"] == "openai_compat"
