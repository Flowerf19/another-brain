"""Crash-safe installer for the pinned ONNX artifacts."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

from filelock import FileLock

from ..errors import ConfigError, ModelNotInstalledError
from .manifest import FILES, MODEL_REPOSITORY, MODEL_REVISION

READY_FILE = "ready.json"


def model_ready(model_dir: Path) -> bool:
    marker = model_dir / READY_FILE
    if not marker.is_file():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return data.get("revision") == MODEL_REVISION and all(
        (model_dir / name).is_file() for name in FILES
    )


def require_model(model_dir: Path) -> Path:
    if not model_ready(model_dir):
        raise ModelNotInstalledError(
            f"embedding model is not installed in {model_dir}; run: another-brain model pull"
        )
    return model_dir


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def install_model(model_dir: Path) -> Path:
    model_dir = Path(model_dir)
    model_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with FileLock(str(model_dir) + ".install.lock", timeout=600):
        if model_ready(model_dir):
            return model_dir
        staging = Path(tempfile.mkdtemp(prefix="another-brain-model-", dir=model_dir.parent))
        try:
            for relative, expected in FILES.items():
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                url = (
                    f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/"
                    f"{MODEL_REVISION}/{relative}"
                )
                print(f"Downloading {relative}", file=sys.stderr, flush=True)
                try:
                    urllib.request.urlretrieve(url, target)
                except Exception as exc:
                    raise ConfigError(f"failed to download {relative}: {exc}") from exc
                actual = _sha256(target)
                if actual != expected:
                    raise ConfigError(
                        f"SHA-256 mismatch for {relative}: got {actual}, expected {expected}"
                    )
            (staging / READY_FILE).write_text(
                json.dumps(
                    {"repository": MODEL_REPOSITORY, "revision": MODEL_REVISION},
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            if model_dir.exists():
                shutil.rmtree(model_dir)
            os.replace(staging, model_dir)
            return model_dir
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
