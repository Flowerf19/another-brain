"""ModelInstaller — explicit or policy-approved model downloads (Step 03).

The only module that talks to the Hugging Face Hub. Every download passes two
gates in order: the network gate (MODEL_ALLOW_NETWORK) and the policy gate
(MODEL_DOWNLOAD_POLICY x trigger). File integrity is verified by
huggingface_hub against repo etags during download; the resolved metadata is
recorded in the cache's meta.json.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from errors import ConfigError
from models.cache import ModelCache
from models.policy import TRIGGER_EXPLICIT, ModelInstallPolicy
from models.registry import ModelSpec
from models.runtime import ModelRuntimeProfile
from models.status import ModelStatus

logger = logging.getLogger(__name__)

_PULL_HINT = "run: another-brain model pull --kind {kind}"


class ModelInstaller:
    def __init__(
        self,
        cache: ModelCache,
        policy: ModelInstallPolicy,
        *,
        allow_network: bool,
        pinned_revision: str = "",
    ):
        self._cache = cache
        self._policy = policy
        self._allow_network = allow_network
        self._revision = pinned_revision.strip()

    # ------------------------------------------------------------------ plan

    def plan(self, spec: ModelSpec) -> dict[str, Any]:
        """Secret-free description of what `model pull` would do."""
        return {
            "model": spec.name,
            "kind": spec.kind,
            "cached": self._cache.is_cached(spec.name),
            "target": str(self._cache.model_dir(spec.name)),
            "revision": self._revision or "latest",
            "expected_dim": spec.expected_dim,
            "download_policy": self._policy.value,
            "network_allowed": self._allow_network,
        }

    # ---------------------------------------------------------------- ensure

    def ensure(self, spec: ModelSpec, trigger: str) -> Path:
        """Return the local model dir, downloading it only when both the
        policy and the network gate allow this trigger."""
        if self._cache.is_cached(spec.name):
            return self._cache.model_dir(spec.name)
        if self._policy is ModelInstallPolicy.DISABLED:
            raise ConfigError(
                f"model {spec.name!r} is not installed and "
                f"MODEL_DOWNLOAD_POLICY=disabled — install it out of band "
                f"into {self._cache.model_dir(spec.name)}"
            )
        if not self._policy.may_download(trigger):
            hint = _PULL_HINT.format(kind=spec.kind)
            raise ConfigError(
                f"model {spec.name!r} is not installed and "
                f"MODEL_DOWNLOAD_POLICY={self._policy.value} does not allow "
                f"downloads on {trigger} — {hint}"
            )
        return self.pull(spec, trigger=trigger)

    # ------------------------------------------------------------------ pull

    def pull(self, spec: ModelSpec, *, trigger: str = TRIGGER_EXPLICIT) -> Path:
        """Explicit, resumable, safe-to-rerun download into the model cache."""
        if self._cache.is_cached(spec.name):
            logger.info("Model %s already cached — nothing to pull", spec.name)
            return self._cache.model_dir(spec.name)
        if self._policy is ModelInstallPolicy.DISABLED:
            raise ConfigError(
                "MODEL_DOWNLOAD_POLICY=disabled — model downloads are refused"
            )
        if not self._allow_network:
            raise ConfigError(
                f"downloading {spec.name!r} requires MODEL_ALLOW_NETWORK=true"
            )

        snapshot_download = _import_snapshot_download()
        target = self._cache.model_dir(spec.name)
        logger.info("Downloading %s -> %s (trigger=%s)", spec.name, target, trigger)
        snapshot_download(
            repo_id=spec.name,
            revision=self._revision or None,
            local_dir=str(target),
        )
        self._cache.write_meta(
            spec.name,
            {
                "provider": "local",
                "model_name": spec.name,
                "kind": spec.kind,
                "revision": self._revision or "latest",
                "expected_dim": spec.expected_dim,
                "query_prompt_name": spec.query_prompt_name,
                "downloaded_at": time.time(),
                "trigger": trigger,
            },
        )
        return target

    # ---------------------------------------------------------------- status

    def status(
        self,
        spec: ModelSpec,
        *,
        provider: str,
        profile: ModelRuntimeProfile | None = None,
    ) -> ModelStatus:
        local = provider == "local"
        return ModelStatus(
            kind=spec.kind,
            provider=provider,
            model_name=spec.name,
            revision=self._revision,
            download_policy=self._policy.value,
            network_allowed=self._allow_network,
            cached=self._cache.is_cached(spec.name) if local else True,
            cache_path=str(self._cache.model_dir(spec.name)) if local else None,
            expected_dim=spec.expected_dim,
            weight_precision=profile.weight_precision if (local and profile) else None,
            device=profile.device if (local and profile) else None,
        )


def _import_snapshot_download():
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        raise ConfigError(
            "local model support requires the 'local' extra: "
            "uv sync --extra local  (or pip install 'another-brain[local]')"
        ) from None
    return snapshot_download
