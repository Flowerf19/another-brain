"""TASK-018/043: installer — download/resume/progress, hash-before-rename,
atomic publish, marker visibility, stale cleanup, concurrent convergence,
and the typed error paths. Served from a local HTTP server; no network."""
from __future__ import annotations

import functools
import hashlib
import http.server
import json
import multiprocessing as mp
import socketserver
import threading
import time
from pathlib import Path

import pytest

from another_brain.errors import ModelDownloadError, ModelHashMismatchError
from another_brain.model_installer import (
    is_installed,
    install,
    profile_dir,
    verify,
)
from another_brain.model_manifest import ModelManifest, manifest_digest

REVISION = "0123456789abcdef0123456789abcdef01234567"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_manifest(files: dict[str, bytes], revision: str = REVISION) -> ModelManifest:
    return ModelManifest(
        profile="test",
        repo="test/harrier",
        revision=revision,
        files=tuple(sorted((name, _sha256_bytes(data)) for name, data in files.items())),
        query_prompt="Q",
        query_prompt_utf8_sha256=_sha256_bytes(b"Q"),
        document_template="d",
        input_version=2,
        dimensions=4,
        dtype="float32",
        normalization="unit_l2",
    )


def _write_tree(root: Path, files: dict[str, bytes]) -> None:
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    server_version = "another-brain-test/1"

    def log_message(self, *args) -> None:  # noqa: D102
        pass


class _AbortOnceHandler(_QuietHandler):
    """Serves the requested file, then closes the connection mid-body once."""

    def do_GET(self) -> None:  # noqa: D102
        server = self.server
        if getattr(server, "abort_path", None) == self.path and not getattr(
            server, "aborted", False
        ):
            server.aborted = True
            data = Path(self.translate_path(self.path)).read_bytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data[: len(data) // 2])
            self.wfile.flush()
            self.connection.close()
            return
        super().do_GET()


class _RequestCountingHandler(_QuietHandler):
    def do_GET(self) -> None:  # noqa: D102
        self.server.requests = getattr(self.server, "requests", 0) + 1
        super().do_GET()


def _start_server(root: Path, handler_cls=_QuietHandler):
    handler = functools.partial(handler_cls, directory=str(root))
    server = socketserver.TCPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture
def served(tmp_path):
    """Local HTTP server over a static tree; yields (server, base_url, root)."""
    root = tmp_path / "www"
    root.mkdir()
    server, base_url = _start_server(root)
    yield server, base_url, root
    server.shutdown()
    server.server_close()


def test_install_downloads_verifies_and_marks(served, tmp_path):
    server, base_url, root = served
    files = {"onnx/model.onnx": b"a" * 300_000, "tokenizer.json": b"t" * 50_000}
    _write_tree(root, files)
    manifest = _make_manifest(files)
    progress: list[tuple[str, int, int | None]] = []

    def collect(name: str, done: int, total: int | None) -> None:
        progress.append((name, done, total))

    path = install(
        tmp_path / "cache", manifest=manifest, base_url=base_url, progress=collect
    )

    assert path == profile_dir(tmp_path / "cache", manifest)
    assert is_installed(tmp_path / "cache", manifest=manifest)
    assert verify(tmp_path / "cache", manifest=manifest) == {n: "ok" for n in files}
    assert (path / ".installed.json").exists()
    marker = json.loads((path / ".installed.json").read_text())
    assert marker["manifest_digest"] == manifest_digest(manifest)
    assert (path / "onnx/model.onnx").read_bytes() == files["onnx/model.onnx"]
    assert progress, "progress reported"
    assert not (tmp_path / "cache" / f"{path.name}.tmp").exists()


def test_idempotent_second_install_skips_downloads(served, tmp_path):
    server, base_url, root = served
    files = {"a.bin": b"y" * 20_000}
    _write_tree(root, files)
    manifest = _make_manifest(files)
    cache = tmp_path / "cache"
    install(cache, manifest=manifest, base_url=base_url)
    install(cache, manifest=manifest, base_url=base_url)  # fast path
    assert is_installed(cache, verify_files=True, manifest=manifest)


def test_converges_when_files_are_partially_in_place(served, tmp_path):
    server, base_url, root = served
    files = {"a.bin": b"1" * 10_000, "b.bin": b"2" * 10_000}
    _write_tree(root, files)
    manifest = _make_manifest(files)
    cache = tmp_path / "cache"
    profile = profile_dir(cache, manifest)
    profile.mkdir(parents=True)
    (profile / "a.bin").write_bytes(files["a.bin"])  # already verified in place
    path = install(cache, manifest=manifest, base_url=base_url)
    assert path == profile
    assert is_installed(cache, verify_files=True, manifest=manifest)


def test_hash_mismatch_rejected_before_rename(served, tmp_path):
    server, base_url, root = served
    files = {"bad.bin": b"good" * 5_000}
    _write_tree(root, files)
    manifest = _make_manifest(files)
    (root / "bad.bin").write_bytes(b"evil" * 5_000)  # server serves wrong bytes
    cache = tmp_path / "cache"
    with pytest.raises(ModelHashMismatchError) as exc:
        install(cache, manifest=manifest, base_url=base_url)
    assert exc.value.name == "bad.bin"
    assert not is_installed(cache, manifest=manifest)
    # never published, part discarded
    assert not (profile_dir(cache, manifest) / "bad.bin").exists()
    tmp = cache / f"{profile_dir(cache, manifest).name}.tmp"
    assert not (tmp / "bad.bin.part").exists()


def test_interrupted_download_resumes_over_range(tmp_path):
    files = {"big.bin": b"z" * 200_000}
    manifest = _make_manifest(files)
    root = tmp_path / "www"
    root.mkdir()
    _write_tree(root, files)
    server, base_url = _start_server(root, _AbortOnceHandler)
    server.abort_path = "/big.bin"
    server.aborted = False
    try:
        cache = tmp_path / "cache"
        with pytest.raises(ModelDownloadError, match="truncated"):
            install(cache, manifest=manifest, base_url=base_url)
        part = cache / f"{profile_dir(cache, manifest).name}.tmp" / "big.bin.part"
        assert part.exists() and part.stat().st_size < 200_000  # partial kept for resume
        assert not is_installed(cache, manifest=manifest)  # never visible
        path = install(cache, manifest=manifest, base_url=base_url)
        assert (path / "big.bin").read_bytes() == files["big.bin"]
        assert is_installed(cache, verify_files=True, manifest=manifest)
    finally:
        server.shutdown()
        server.server_close()


def test_restart_from_scratch_on_unsatisfiable_range(tmp_path):
    files = {"f.bin": b"r" * 30_000}
    manifest = _make_manifest(files)
    root = tmp_path / "www"
    root.mkdir()
    _write_tree(root, files)
    server, base_url = _start_server(root)
    try:
        cache = tmp_path / "cache"
        profile = profile_dir(cache, manifest)
        part = cache / f"{profile.name}.tmp" / "f.bin.part"
        part.parent.mkdir(parents=True)
        part.write_bytes(b"\x00" * 30_000)  # full size but corrupt → Range past EOF → 416
        path = install(cache, manifest=manifest, base_url=base_url)
        assert (path / "f.bin").read_bytes() == files["f.bin"]
    finally:
        server.shutdown()
        server.server_close()


def test_stale_tmp_cleaned(served, tmp_path):
    server, base_url, root = served
    files = {"a.bin": b"s" * 10_000}
    _write_tree(root, files)
    manifest = _make_manifest(files)
    cache = tmp_path / "cache"
    tmp = cache / f"{profile_dir(cache, manifest).name}.tmp"
    tmp.mkdir(parents=True)
    (tmp / "old.part").write_bytes(b"junk")
    old = time.time() - 7200
    os_utime = (old, old)
    import os

    os.utime(tmp, os_utime)
    install(cache, manifest=manifest, base_url=base_url, stale_tmp_age_s=3600)
    assert not (tmp / "old.part").exists()
    assert is_installed(cache, manifest=manifest)


def test_fresh_part_survives_stale_cleanup(served, tmp_path):
    server, base_url, root = served
    manifest = _make_manifest({"a.bin": b"x" * 10_000})  # file NOT on server → 404
    cache = tmp_path / "cache"
    tmp = cache / f"{profile_dir(cache, manifest).name}.tmp"
    tmp.mkdir(parents=True)
    (tmp / "a.bin.part").write_bytes(b"partial")
    with pytest.raises(ModelDownloadError, match="HTTP 404"):
        install(cache, manifest=manifest, base_url=base_url, stale_tmp_age_s=3600)
    # fresh part: survived stale cleanup AND the failed attempt (kept for resume)
    assert (tmp / "a.bin.part").read_bytes() == b"partial"


def test_marker_removal_makes_profile_invisible(served, tmp_path):
    server, base_url, root = served
    files = {"a.bin": b"m" * 10_000}
    _write_tree(root, files)
    manifest = _make_manifest(files)
    cache = tmp_path / "cache"
    path = install(cache, manifest=manifest, base_url=base_url)
    assert is_installed(cache, verify_files=False, manifest=manifest)
    (path / ".installed.json").unlink()
    assert not is_installed(cache, verify_files=False, manifest=manifest)
    assert verify(cache, manifest=manifest)["a.bin"] == "ok"  # files fine, marker gone


def test_marker_digest_drift_makes_profile_invisible(served, tmp_path):
    server, base_url, root = served
    files = {"a.bin": b"d" * 10_000}
    _write_tree(root, files)
    manifest = _make_manifest(files)
    cache = tmp_path / "cache"
    path = install(cache, manifest=manifest, base_url=base_url)
    marker = path / ".installed.json"
    payload = json.loads(marker.read_text())
    payload["manifest_digest"] = "0" * 64
    marker.write_text(json.dumps(payload))
    assert not is_installed(cache, manifest=manifest)


def _spawn_worker(cache_dir: str, url: str, manifest: ModelManifest, outq) -> None:
    """Module-level worker so multiprocessing spawn can pickle it."""
    import another_brain.model_installer as mi

    try:
        path = mi.install(cache_dir, manifest=manifest, base_url=url)
        outq.put(("ok", str(path), mi.is_installed(cache_dir, verify_files=True, manifest=manifest)))
    except Exception as exc:  # noqa: BLE001
        outq.put(("error", repr(exc), None))


def test_concurrent_installers_converge(served, tmp_path):
    """Four spawned processes install into the same cache; all converge."""
    server, base_url, root = served
    files = {"c.bin": b"c" * 80_000, "d.bin": b"e" * 60_000}
    _write_tree(root, files)
    manifest = _make_manifest(files)
    cache = tmp_path / "cache"

    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    procs = [ctx.Process(target=_spawn_worker, args=(str(cache), base_url, manifest, queue)) for _ in range(4)]
    for p in procs:
        p.start()
    results = [queue.get(timeout=120) for _ in procs]
    for p in procs:
        p.join(timeout=30)
    assert all(code == "ok" for code, _, _ in results), results
    assert len({path for _, path, _ in results}) == 1  # same profile dir
    assert all(installed for _, _, installed in results)
    assert is_installed(cache, verify_files=True, manifest=manifest)
    assert not (cache / f"{profile_dir(cache, manifest).name}.tmp").exists()
