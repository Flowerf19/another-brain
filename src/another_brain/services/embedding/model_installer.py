"""Model installer — download + idempotent crash-safe install (TASK-018/043).

Fetches exactly the five pinned runtime files of the locked q4 manifest into
the per-user cache (``platformdirs`` cache dir) over plain HTTPS, with no
extra dependency beyond the standard library + filelock:

- one cross-process lock per manifest (``<profile>.lock``);
- downloads land in a sibling ``<profile>.tmp`` directory as ``.part`` files
  and resume via HTTP Range after an interrupted attempt;
- stale temporary state older than an hour is cleaned on the next install;
- SHA-256 is verified *before* any rename into the profile directory;
- the profile directory is immutable per revision and only becomes visible
  (``is_installed``) when the ``.installed.json`` marker is written last —
  a partially installed profile is never observable by another process;
- concurrent installers serialize on the lock and converge: already-verified
  files in place are reused, only missing/corrupt files are (re-)downloaded.

The module never imports onnxruntime/tokenizers, so ``model status`` answers
without loading the model (TASK-046).
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from filelock import FileLock

from another_brain.errors import ModelDownloadError, ModelHashMismatchError
from another_brain.services.embedding.model_manifest import MODEL_MANIFEST, ModelManifest, manifest_digest

CHUNK_BYTES = 1 << 20
DEFAULT_LOCK_TIMEOUT_S = 900.0
STALE_TMP_AGE_S = 3600.0
MARKER_NAME = ".installed.json"
USER_AGENT = "another-brain/0.11.0"

ProgressFn = Callable[[str, int, int | None], None]


def profile_dir_name(manifest: ModelManifest = MODEL_MANIFEST) -> str:
    """Immutable per-revision profile directory name."""
    return f"harrier-{manifest.profile}-{manifest.revision}"


def profile_dir(cache_dir: Path, manifest: ModelManifest = MODEL_MANIFEST) -> Path:
    return Path(cache_dir) / profile_dir_name(manifest)


def tmp_dir(cache_dir: Path, manifest: ModelManifest = MODEL_MANIFEST) -> Path:
    return Path(cache_dir) / f"{profile_dir_name(manifest)}.tmp"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(cache_dir: Path, manifest: ModelManifest = MODEL_MANIFEST) -> dict[str, str]:
    """Per-file state of the profile: ``ok`` | ``missing`` | ``mismatch``.

    Marker not required — answers ``model status`` and drives install
    convergence. Reads only, no lock, no model load.
    """
    profile = profile_dir(cache_dir, manifest)
    states: dict[str, str] = {}
    for name, expected in manifest.files:
        path = profile / name
        if not path.exists():
            states[name] = "missing"
        elif sha256_file(path) != expected:
            states[name] = "mismatch"
        else:
            states[name] = "ok"
    return states


def is_installed(
    cache_dir: Path,
    *,
    verify_files: bool = False,
    manifest: ModelManifest = MODEL_MANIFEST,
) -> bool:
    """A profile is installed iff the marker exists with our manifest digest.

    ``verify_files=True`` additionally requires every pinned file to hash
    match (used by install idempotency and doctor).
    """
    marker = profile_dir(cache_dir, manifest) / MARKER_NAME
    if not marker.exists():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if payload.get("manifest_digest") != manifest_digest(manifest):
        return False
    if not verify_files:
        return True
    return all(state == "ok" for state in verify(cache_dir, manifest).values())


def _default_base_url(manifest: ModelManifest) -> str:
    return f"https://huggingface.co/{manifest.repo}/resolve/{manifest.revision}"


def _download_file(
    url: str, dest: Path, name: str, progress: ProgressFn | None
) -> None:
    """Download ``url`` into ``dest``, resuming from ``dest``'s current size."""
    resume = dest.stat().st_size if dest.exists() else 0
    headers = {"User-Agent": USER_AGENT}
    if resume:
        headers["Range"] = f"bytes={resume}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=30)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and resume:  # range unsatisfiable: restart from scratch
            dest.unlink(missing_ok=True)
            return _download_file(url, dest, name, progress)
        raise ModelDownloadError(f"HTTP {exc.code} for {url}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ModelDownloadError(f"cannot reach {url}: {exc}") from exc

    status = response.status
    mode = "ab" if status == 206 else "wb"
    done = resume if status == 206 else 0
    total: int | None = None
    content_length = response.headers.get("Content-Length")
    if content_length:
        total = (resume if status == 206 else 0) + int(content_length)
    try:
        with open(dest, mode) as fh:
            while True:
                chunk = response.read(CHUNK_BYTES)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if progress:
                    progress(name, done, total)
        # A clean FIN mid-body reads as EOF, not an exception: only a
        # length mismatch reveals the truncation. Keep the partial file
        # so the next attempt resumes over Range.
        if total is not None and done != total:
            raise ModelDownloadError(
                f"truncated download of {name}: got {done} of {total} bytes"
            )
    except (OSError, http.client.HTTPException) as exc:
        # interrupted mid-body: keep the partial file so the next attempt
        # resumes over Range; only a hash mismatch discards it.
        raise ModelDownloadError(f"interrupted download of {name}: {exc}") from exc
    finally:
        response.close()


def _cleanup_stale_tmp(cache_dir: Path, manifest: ModelManifest, stale_tmp_age_s: float) -> None:
    tmp = tmp_dir(cache_dir, manifest)
    if not tmp.exists():
        return
    try:
        age = time.monotonic() - tmp.stat().st_mtime
    except OSError:
        return
    if age > stale_tmp_age_s:
        shutil.rmtree(tmp, ignore_errors=True)


def _write_marker(profile: Path, manifest: ModelManifest) -> None:
    payload = {
        "profile": manifest.profile,
        "revision": manifest.revision,
        "manifest_digest": manifest_digest(manifest),
        "installed_at_ms": int(time.time() * 1000),
    }
    tmp = profile / f"{MARKER_NAME}.tmp"
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, profile / MARKER_NAME)


def install(
    cache_dir: Path,
    *,
    manifest: ModelManifest = MODEL_MANIFEST,
    base_url: str | None = None,
    progress: ProgressFn | None = None,
    lock_timeout_s: float = DEFAULT_LOCK_TIMEOUT_S,
    stale_tmp_age_s: float = STALE_TMP_AGE_S,
) -> Path:
    """Idempotent, crash-safe install; returns the profile directory.

    ``base_url`` overrides the default ``https://huggingface.co/…`` endpoint
    (test seam and mirror support). Progress reports ``(name, done, total)``
    per chunk; the caller decides how to render it (stderr, throttled).
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    profile = profile_dir(cache_dir, manifest)
    lock = FileLock(str(cache_dir / f"{profile.name}.lock"), timeout=lock_timeout_s)
    with lock:
        _cleanup_stale_tmp(cache_dir, manifest, stale_tmp_age_s)
        if is_installed(cache_dir, verify_files=True, manifest=manifest):
            return profile  # idempotent fast path
        if base_url is None:
            base_url = _default_base_url(manifest)
        profile.mkdir(parents=True, exist_ok=True)
        tmp = tmp_dir(cache_dir, manifest)
        for name, expected in manifest.files:
            final = profile / name
            if final.exists() and sha256_file(final) == expected:
                continue  # convergence: verified in place by a prior installer
            part = tmp / f"{name}.part"
            part.parent.mkdir(parents=True, exist_ok=True)
            _download_file(f"{base_url}/{name}", part, name, progress)
            actual = sha256_file(part)
            if actual != expected:
                part.unlink(missing_ok=True)
                raise ModelHashMismatchError(name, expected, actual)
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(part, final)  # atomic: complete + verified or nothing
        _write_marker(profile, manifest)  # last: profile becomes visible
        shutil.rmtree(tmp, ignore_errors=True)
        return profile
