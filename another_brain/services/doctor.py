"""Diagnostic probe service for ``another-brain doctor`` (TASK-084).

The doctor is a read-mostly report card: package, platform, resolved paths,
model files, and the *real* database are all inspected without mutation, and
the write-capable path runs only against a throwaway temporary database
(``tempfile.TemporaryDirectory``) so a broken install can never corrupt the
user's real store.

Read-only contract (the doctor must run in EVERY degradation state):

- never loads the embedding model — no onnxruntime, tokenizers, or numpy
  import happens here (the sqlite-vec probe imports ``sqlite_vec`` only,
  and only to answer capability, never to run vectors);
- never downloads anything — ``model_installer.verify`` hashes files that
  already exist on disk and is a pure read;
- never writes to the real profile — the real-database item opens
  ``mode=ro`` and runs ``PRAGMA`` checks only; all writes happen inside the
  throwaway temp database of the isolated probe.

Every item is (name, status, detail, hint): status is ``ok``/``warn``/
``fail``, detail is one human line, hint is the actionable next step when
status is not ok. ``run()`` never raises: a broken platform, corrupt
database, or failing probe is reported as an item, so the CLI can always
render the full report and exit with a meaningful code.
"""
from __future__ import annotations

import importlib.metadata
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from another_brain.errors import StorageError
from another_brain.services.sql.connection import SQLiteConnectionFactory
from another_brain.services.sql.migrations import migrate
from another_brain.services.sql.schema import SCHEMA_VERSION

if TYPE_CHECKING:
    from another_brain.config import AppConfig

#: The pinned embedding profile's name (locked manifest, TASK-042).
PROFILE = "q4"

#: Diagnostic self-test markers that the probe writes into the throwaway DB.
_PROBE_TOPIC = "doctor-probe"
_PROBE_SUMMARY = "probe payload for write/read/delete verification"
_PROBE_CONTENT = "probe content with the distinctive word: unblinking"


@dataclass(frozen=True)
class DoctorItem:
    """One report line: name, status, one-line detail, actionable hint."""

    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str
    hint: str = ""


@dataclass(frozen=True)
class DoctorReport:
    """The full report; every item is rendered, nothing is dropped."""

    items: tuple[DoctorItem, ...]

    @property
    def failed(self) -> bool:
        return any(item.status == "fail" for item in self.items)


def run(config: "AppConfig") -> DoctorReport:
    """Run every check in order and return the full report."""
    return DoctorReport(
        items=(
            platform_item(),
            paths_item(config),
            package_item(),
            model_item(config),
            database_item(config),
            probe_item(config),
        )
    )


# ---------------------------------------------------------------------------
# platform / paths / package


def platform_item() -> DoctorItem:
    """Support-tier verdict from the platform probe service (TASK-092)."""
    from another_brain.services.system import current_system

    report = current_system()
    detail = (
        f"{report.tier} — {report.reason} ("
        f"{report.os_family} {report.arch}"
        + (f", libc {report.libc}" if report.libc != "none" else "")
        + f", python {report.python_version})"
    )
    if report.tier == "supported":
        return DoctorItem("platform", "ok", detail)
    if report.tier == "best_effort":
        return DoctorItem(
            "platform", "warn", detail,
            "the platform resolves but is not CI-gated; expect occasional rough edges",
        )
    if report.tier == "uninstallable":
        return DoctorItem(
            "platform", "fail", detail,
            "sqlite-vec and onnxruntime ship no wheels here; the tool cannot install",
        )
    return DoctorItem(
        "platform", "fail", detail,
        "unsupported platform; this build cannot run the embedding stack here",
    )


def paths_item(config: "AppConfig") -> DoctorItem:
    """Resolved data/model paths, noting BRAIN_* overrides when set."""
    data_dir = config.data_dir
    db_path = config.database_path
    model_cache = config.model_cache_dir
    detail = f"data {data_dir} | db {db_path} | model cache {model_cache}"
    if os.environ.get("BRAIN_DATA_DIR"):
        detail += " | BRAIN_DATA_DIR set"
    if os.environ.get("BRAIN_MODEL_CACHE_DIR"):
        detail += " | BRAIN_MODEL_CACHE_DIR set"
    return DoctorItem("paths", "ok", detail)


def package_item() -> DoctorItem:
    """Installed distribution version and location (importlib.metadata)."""
    try:
        dist = importlib.metadata.distribution("another-brain")
        version = dist.version
        location = str(Path(dist.locate_file("")).resolve())
    except importlib.metadata.PackageNotFoundError:
        return DoctorItem(
            "package", "warn", "another-brain not installed as a distribution",
            "install via: python -m pip install another-brain (or: uv tool install another-brain)",
        )
    except Exception as exc:  # noqa: BLE001 - report, never raise
        return DoctorItem(
            "package", "warn", f"package metadata unreadable: {exc}",
            "reinstall via: python -m pip install another-brain (or: uv tool install another-brain)",
        )
    return DoctorItem("package", "ok", f"another-brain {version} at {location}")


# ---------------------------------------------------------------------------
# model


def model_item(config: "AppConfig") -> DoctorItem:
    """Per-file SHA-256 state via the installer's own verification.

    Reuses :func:`~another_brain.services.embedding.model_installer.verify`
    and :func:`~another_brain.services.embedding.model_installer.is_installed`
    so hashing and marker logic are exactly what ``model status`` and
    ``model pull`` rely on — never re-implemented here. Reads only, no lock,
    no model load.
    """
    from another_brain.services.embedding.model_installer import (
        is_installed,
        profile_dir,
        verify,
    )

    states = verify(config.model_cache_dir)
    if all(state == "ok" for state in states.values()) and is_installed(
        config.model_cache_dir, verify_files=True
    ):
        return DoctorItem(
            "model", "ok",
            f"profile {PROFILE} installed ({len(states)}/{len(states)} files verified)",
        )
    bad = [name for name, state in states.items() if state != "ok"]
    missing = [name for name, state in states.items() if state == "missing"]
    corrupt = [name for name, state in states.items() if state == "mismatch"]
    location = profile_dir(config.model_cache_dir)
    if corrupt:
        return DoctorItem(
            "model", "fail",
            f"SHA-256 mismatch in {len(corrupt)} file(s) under {location}",
            "corrupt files are rejected at load; re-run: another-brain model pull",
        )
    return DoctorItem(
        "model", "warn",
        f"profile {PROFILE} not installed "
        + (f"({len(missing)} file(s) missing)" if missing else "(no files)")
        + f" in {location}",
        "recent/admin/connect still work; run: another-brain model pull",
    )


# ---------------------------------------------------------------------------
# database (real, read-only)


def database_item(config: "AppConfig") -> DoctorItem:
    """Open the real database READ-ONLY when it exists; never writes.

    Missing database is a warn (created on first use, and ``recent`` and
    ``connect`` work without one), never a fail. A file that exists but
    cannot be opened — corrupt bytes, foreign page size, unmigrated or
    partial schema, ledger mismatch — is a fail with the concrete reason.
    """
    db_path = config.database_path
    if not db_path.exists():
        return DoctorItem(
            "database", "warn",
            f"no database yet — {db_path} is created on first use",
            "start the server once (another-brain), or run the isolated probe to verify the stack",
        )
    try:
        with SQLiteConnectionFactory(db_path).connect(read_only=True) as con:
            raw = con.connection
            checks = []
            row = raw.execute("PRAGMA integrity_check").fetchone()
            if bool(row) and row[0] == "ok":
                checks.append("integrity ok")
            else:
                raise StorageError(f"integrity_check failed: {row[0] if row else 'no result'}")
            fk_rows = raw.execute("PRAGMA foreign_key_check").fetchall()
            if fk_rows:
                raise StorageError(
                    f"foreign_key_check: {len(fk_rows)} violation(s), first: {fk_rows[0]}"
                )
            checks.append("foreign keys ok")
            version = raw.execute("PRAGMA user_version").fetchone()[0]
            checks.append(f"schema v{version}")
            mode = raw.execute("PRAGMA journal_mode").fetchone()[0]
            checks.append(f"journal {mode}")
            page_size = raw.execute("PRAGMA page_size").fetchone()[0]
            checks.append(f"page_size {page_size}")
    except Exception as exc:  # noqa: BLE001 - report, never raise
        return DoctorItem(
            "database", "fail", f"{db_path}: {exc}",
            "the store cannot be opened; see the error above — reinstall or restore the file",
        )
    detail = f"{db_path} | {' | '.join(checks)}"
    if version != SCHEMA_VERSION:
        return DoctorItem(
            "database", "fail", detail,
            f"schema version {version} != expected {SCHEMA_VERSION}; upgrade this build",
        )
    return DoctorItem("database", "ok", detail)


# ---------------------------------------------------------------------------
# probe (isolated, write-capable)


class _ProbeFailure(Exception):
    """Any step of the isolated write probe; carries one human line."""


def probe_item(config: "AppConfig") -> DoctorItem:
    """Full bootstrap/migrate/write/read/delete/FTS5/vec exercise in a
    THROWAWAY temp database — the only item that writes anything, and it
    never touches the real profile.

    Uses the product's own connection factory and migration runner so the
    probe validates exactly the code path a real store takes, then deletes
    the row and closes. The temp directory is removed on exit regardless of
    outcome (``TemporaryDirectory`` context manager).
    """
    checks: list[str] = []
    try:
        with tempfile.TemporaryDirectory(prefix="another-brain-doctor-") as td:
            tmp = Path(td)
            checks.append(f"tempfile {tmp.name}")
            factory = SQLiteConnectionFactory(
                tmp / "probe.sqlite3", disable_vec=config.disable_sqlite_vec
            )
            factory.bootstrap()
            checks.append("bootstrap ok")
            version = migrate(factory.db_path)
            if version != SCHEMA_VERSION:
                raise _ProbeFailure(
                    f"migrations applied to v{version}, expected v{SCHEMA_VERSION}"
                )
            checks.append(f"migrations to v{version}")
            _register_probe_profile(factory)  # same contract the server open uses
            checks.append("profile registered")

            with factory.connect() as con:
                raw = con.connection
                # FTS5 availability: build + query the virtual table against
                # the schema's external-content definition.
                raw.execute("INSERT INTO memory_fts(memory_fts) VALUES ('rebuild')")
                row = raw.execute(
                    "SELECT count(*) FROM memory_fts WHERE memory_fts MATCH 'unblinking'"
                ).fetchone()
                fts_ok = bool(row) and row[0] == 0
            if not fts_ok:
                raise _ProbeFailure("FTS5 virtual table does not accept a MATCH query")
            checks.append("fts5 query ok")

            vec = factory.connect(read_only=True)
            try:
                vec_loaded = vec.load_vec()
                vec_error = vec.vec_load_error
            finally:
                vec.close()
            if vec_loaded:
                checks.append("sqlite-vec loaded")
            else:
                reason = f" ({vec_error})" if vec_error else ""
                checks.append(f"sqlite-vec unavailable — NumPy fallback{reason}")

            _probe_row(factory, checks)
            return DoctorItem(
                "probe", "ok" if vec_loaded else "warn",
                f"{' | '.join(checks)}",
                "" if vec_loaded
                else "sqlite-vec failed to load; the exact NumPy fallback runs instead (slower at scale)",
            )
    except _ProbeFailure as exc:
        return DoctorItem(
            "probe", "fail", str(exc),
            "the storage stack cannot bootstrap in isolation; reinstall the tool",
        )
    except Exception as exc:  # noqa: BLE001 - report, never raise
        return DoctorItem(
            "probe", "fail", f"isolated probe failed: {exc}",
            "unexpected failure; reinstall the tool",
        )


def _register_probe_profile(factory: SQLiteConnectionFactory) -> None:
    """Insert the manifest profile row with plain SQL, mirroring
    ``register_profile``'s contract, without importing numpy through
    ``domain.models``. The profile row must exist for ``memories.profile_id``
    to satisfy its foreign key — the same gate the server's open path seeds.
    """
    from another_brain.services.embedding.model_manifest import MODEL_MANIFEST

    files = dict(MODEL_MANIFEST.files)
    with factory.connect() as con:
        raw = con.connection
        raw.execute(
            "INSERT INTO embedding_profiles(profile_id, model_repo, model_revision,"
            " variant, dimension, dtype, normalized, tokenizer_sha256, config_sha256,"
            " prompt_utf8_sha256, query_prompt, input_version, created_at_ms)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                MODEL_MANIFEST.profile, MODEL_MANIFEST.repo, MODEL_MANIFEST.revision,
                MODEL_MANIFEST.profile, MODEL_MANIFEST.dimensions,
                MODEL_MANIFEST.dtype,
                1 if MODEL_MANIFEST.normalization == "unit_l2" else 0,
                files["tokenizer.json"], files["config.json"],
                MODEL_MANIFEST.query_prompt_utf8_sha256,
                MODEL_MANIFEST.query_prompt, MODEL_MANIFEST.input_version,
                1,
            ),
        )
        raw.commit()


def _probe_row(factory: SQLiteConnectionFactory, checks: list[str]) -> None:
    """Insert/read/delete one row through a plain connection, plus a tiny
    lexical FTS query against it. Pure sqlite3: no numpy/onnxruntime."""
    with factory.connect() as con:
        raw = con.connection
        row_id = 1
        raw.execute(
            "INSERT INTO memories(row_id, memory_id, brain_id, agent_id, topic,"
            " catalog, summary, content, timeline_day, created_at_ms, updated_at_ms,"
            " importance, expires_at_ms, metadata, profile_id, embedding, record_version)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row_id, "probe-memory", "probe-brain", "probe-agent",
                _PROBE_TOPIC, "probe", _PROBE_SUMMARY, _PROBE_CONTENT,
                "2026-01-01", 1, 1, 3, 1,
                json.dumps({"doctor": True}), PROFILE, bytes(2560), 1,
            ),
        )
        rows = raw.execute(
            "SELECT topic FROM memories WHERE memory_id = 'probe-memory'"
        ).fetchall()
        if not rows or rows[0][0] != _PROBE_TOPIC:
            raise _ProbeFailure("probe row write/read round-trip failed")
        found = raw.execute(
            "SELECT count(*) FROM memory_fts WHERE memory_fts MATCH 'unblinking'"
        ).fetchone()[0]
        if found != 1:
            raise _ProbeFailure(f"probe FTS query expected 1 hit, got {found}")
        deleted = raw.execute(
            "DELETE FROM memories WHERE memory_id = 'probe-memory'"
        ).rowcount
        if deleted != 1:
            raise _ProbeFailure("probe row delete failed")
    checks.append("insert/read/delete + FTS hit ok")
