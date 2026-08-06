"""Console entry point and command parser (TASK-040).

Transport contract: bare ``another-brain`` is always the MCP stdio server —
stdout is reserved exclusively for MCP frames; all logs, progress, and
diagnostics go to stderr. HTTP is opt-in via ``serve --http`` with bind
precedence CLI ``--host/--port`` > ``MCP_HTTP_HOST``/``MCP_HTTP_PORT`` >
``127.0.0.1:1905``, numeric loopback only (validated by
:mod:`another_brain.config`).

``doctor`` still lands in a later phase (GOAL-016); until then it validates
its arguments, then exits ``EXIT_UNAVAILABLE`` with a typed not-yet-available
message on stderr. ``recent`` and ``admin`` are live and need no embedding
model: they open the store with a no-op budget validator (token budgets only
matter on the write/search path), so listing or administering memories works
without a model download.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from another_brain.config import AppConfig, parse_loopback_host, parse_port
from another_brain.errors import (
    ConfigError,
    ModelInstallError,
    ModelNotInstalledError,
    StorageError,
)

if TYPE_CHECKING:
    from another_brain.domain.models import MemoryRecord
    from another_brain.services.memory_service import MemoryService

PROG = "another-brain"
VERSION = "0.11.0"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_UNAVAILABLE = 3
EXIT_CONFIG = 4


class _NotAvailable(Exception):
    """The command is valid but its subsystem has not landed yet."""

    def __init__(self, command: str, phase: str) -> None:
        super().__init__(command)
        self.command = command
        self.phase = phase


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Shared long-term memory for MCP agents. Bare invocation starts the "
            "MCP stdio server (stdout carries MCP frames only)."
        ),
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {VERSION}")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser(
        "serve", help="start the MCP server (stdio default, --http opt-in)"
    )
    serve.add_argument(
        "--http",
        action="store_true",
        help="serve Streamable HTTP on a numeric loopback address instead of stdio",
    )
    serve.add_argument("--host", default=None, help="numeric loopback IP (127.0.0.0/8 or ::1)")
    serve.add_argument("--port", default=None, type=str, help="TCP port 1..65535")

    model = sub.add_parser("model", help="manage the pinned embedding model")
    model_sub = model.add_subparsers(dest="model_command")
    model_sub.add_parser("pull", help="download and verify the pinned q4 artifacts")
    model_sub.add_parser("status", help="show install/load state without loading the model")

    sub.add_parser("doctor", help="verify install, model, and database health")
    recent = sub.add_parser("recent", help="print recent memories (uses the configured data dir)")
    recent.add_argument(
        "--limit",
        type=_recent_limit,
        default=20,
        metavar="N",
        help="max rows to print (1..100, default 20)",
    )

    admin = sub.add_parser("admin", help="administrative lifecycle operations")
    admin_sub = admin.add_subparsers(dest="admin_command")
    restore = admin_sub.add_parser("restore", help="undo a soft delete inside its grace window")
    restore.add_argument("memory_id")
    hard = admin_sub.add_parser("hard-delete", help="permanently remove a memory")
    hard.add_argument("memory_id")

    imp = sub.add_parser("import-jsonl", help="import a another-brain-jsonl v1 artifact")
    imp.add_argument("path", help="path to the JSONL export artifact")
    return parser


def _err(message: str) -> None:
    print(f"{PROG}: {message}", file=sys.stderr)


def _recent_limit(value: str) -> int:
    """argparse type for ``recent --limit``: an integer in 1..100."""
    try:
        limit = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--limit must be an integer, got {value!r}"
        ) from None
    if not 1 <= limit <= 100:  # RECENT_LIMIT_MAX (services.memory_service)
        raise argparse.ArgumentTypeError("--limit must be between 1 and 100")
    return limit


def _progress(name: str, done: int, total: int | None) -> None:
    """Throttled single-line progress on stderr (stdout is protocol-clean)."""
    if total:
        print(f"\r  {name}: {done / 1e6:.1f}/{total / 1e6:.1f} MiB", end="", file=sys.stderr)
    else:
        print(f"\r  {name}: {done / 1e6:.1f} MiB", end="", file=sys.stderr)


def _resolve_http_bind(args: argparse.Namespace, config: AppConfig) -> tuple[str, int]:
    """CLI flag > environment > default, all numeric-loopback validated."""
    host = parse_loopback_host(args.host) if args.host is not None else config.http.host
    port = parse_port(args.port, source="--port") if args.port is not None else config.http.port
    return host, port


def _dispatch(args: argparse.Namespace, config: AppConfig) -> int:
    if args.command is None:
        return _cmd_serve(argparse.Namespace(http=False, host=None, port=None), config)
    if args.command == "serve":
        return _cmd_serve(args, config)
    if args.command == "model":
        if args.model_command == "pull":
            return _cmd_model_pull(config)
        if args.model_command == "status":
            return _cmd_model_status(config)
    if args.command == "doctor":
        raise _NotAvailable("doctor", "GOAL-016")
    if args.command == "recent":
        return _cmd_recent(args, config)
    if args.command == "admin":
        if args.admin_command == "restore":
            return _cmd_admin_restore(args, config)
        if args.admin_command == "hard-delete":
            return _cmd_admin_hard_delete(args, config)
    if args.command == "import-jsonl":
        return _cmd_import_jsonl(args, config)
    return EXIT_USAGE


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        config = AppConfig.from_env()
        return _dispatch(args, config)
    except _NotAvailable as exc:
        _err(f"{exc.command!r} is not yet available in this build (lands in {exc.phase})")
        return EXIT_UNAVAILABLE
    except ConfigError as exc:
        _err(f"configuration error: {exc}")
        return EXIT_CONFIG


def _cmd_serve(args: argparse.Namespace, config: AppConfig) -> int:
    """Start the MCP server; stdio unless ``--http`` opts in (TASK-067)."""
    from another_brain.mcp.server import serve_http, serve_stdio

    try:
        if args.http:
            host, port = _resolve_http_bind(args, config)
            _err(f"serving MCP on http://{host}:{port}/mcp")
            serve_http(config, host=host, port=port)
        else:
            serve_stdio(config)
    except ModelNotInstalledError as exc:
        _err(str(exc))
        return EXIT_UNAVAILABLE
    except StorageError as exc:
        _err(f"storage error: {exc}")
        return EXIT_ERROR
    return EXIT_OK


def _cmd_model_pull(config: AppConfig) -> int:
    """Download + verify the pinned q4 profile into the cache (TASK-018/043)."""
    from another_brain.services.embedding.model_installer import install as install_model
    from another_brain.services.embedding.model_manifest import MODEL_MANIFEST

    print(f"pulling {MODEL_MANIFEST.profile} @ {MODEL_MANIFEST.revision[:12]}…", file=sys.stderr)
    try:
        path = install_model(config.model_cache_dir, progress=_progress)
    except ModelInstallError as exc:
        print(file=sys.stderr)
        _err(f"model pull failed: {exc}")
        return EXIT_ERROR
    print(file=sys.stderr)  # close the progress line
    print(f"model installed: {path}")
    return EXIT_OK


def _cmd_import_jsonl(args: argparse.Namespace, config: AppConfig) -> int:
    """Import one JSONL v1 artifact into the configured data dir (TASK-071).

    Exit codes: ``EXIT_OK`` on a completed/noop import; ``EXIT_ERROR`` for a
    rejected envelope (:class:`JsonlEnvelopeError`) or an identity/field
    conflict (:class:`JsonlImportConflictError`) — the import failed, which
    ``EXIT_ERROR`` already names, so no new constant is warranted;
    ``EXIT_UNAVAILABLE`` for a missing model, mirroring ``serve``.

    The report goes to stdout (the status style of ``model status``);
    progress/diagnostics go to stderr.
    """
    import time
    from pathlib import Path

    from another_brain.services.embedding.model_installer import profile_dir
    from another_brain.services.embedding.provider import ONNXEmbeddingProvider
    from another_brain.services.jsonl_import import (
        ImportReport,
        JsonlEnvelopeError,
        JsonlImportConflictError,
        JsonlImporter,
    )
    from another_brain.services.sql.connection import SQLiteConnectionFactory
    from another_brain.services.sql.migrations import migrate

    config.ensure_directories()
    factory = SQLiteConnectionFactory(config.database_path)
    factory.bootstrap()
    migrate(config.database_path)
    importer = JsonlImporter(
        factory,
        embedder=ONNXEmbeddingProvider(profile_dir(config.model_cache_dir)),
        clock=lambda: int(time.time() * 1000),
    )
    try:
        report = importer.import_path(Path(args.path))
    except (JsonlEnvelopeError, FileNotFoundError) as exc:
        _err(f"invalid JSONL v1 envelope: {exc}")
        return EXIT_ERROR
    except JsonlImportConflictError as exc:
        _err(f"import conflict: {exc}")
        return EXIT_ERROR
    except ModelNotInstalledError as exc:
        _err(str(exc))
        return EXIT_UNAVAILABLE
    _print_import_report(report)
    return EXIT_OK


def _print_import_report(report: ImportReport) -> None:
    """Human-readable import report on stdout (status style, like model status)."""
    short = report.export_id[:8]
    if report.status == "noop":
        print(
            f"import {short}: status noop — already imported; persisted counters"
            f" imported {report.imported_count}, skipped {report.skipped_count}"
        )
        return
    print(
        f"import {short}: status completed — imported {report.imported_count},"
        f" skipped {report.skipped_count} (artifact {report.artifact_sha256[:12]}…)"
    )


class _NullBudgets:
    """Budget-validator stand-in for commands that never validate input text.

    ``recent``, ``admin restore``, and ``admin hard-delete`` never embed and
    never write new text, so the tokenizer-backed validator (which needs the
    ``tokenizer.json`` file and fails when the model is uninstalled) would be
    dead weight: requiring a ~few-MB tokenizer download just to LIST memories
    is wrong UX. These methods raise so routing a real budget check here
    fails loudly instead of being silently skipped.
    """

    def validate_remember(self, *, topic: str, summary: str, content: str) -> None:
        raise RuntimeError("not usable from this command")

    def validate_query(self, query: str) -> None:
        raise RuntimeError("not usable from this command")


def _open_store(config: AppConfig) -> "MemoryService":
    """Open the store like :func:`another_brain.mcp.server.build_runtime`.

    Same storage path (bootstrap, migrate, register_profile) and the same
    lazy :class:`ONNXEmbeddingProvider` — but the budgets are
    :class:`_NullBudgets`: these commands never embed, so the tokenizer load
    (and its uninstalled-model error) must not stand in the way.
    """
    from another_brain.retrieval.service import HybridMemoryRetriever
    from another_brain.services.embedding.model_installer import profile_dir
    from another_brain.services.embedding.provider import ONNXEmbeddingProvider
    from another_brain.services.memory_service import MemoryService
    from another_brain.services.sql.audit import SQLiteAuditRepository
    from another_brain.services.sql.connection import SQLiteConnectionFactory
    from another_brain.services.sql.health import SQLiteHealthProbe
    from another_brain.services.sql.migrations import migrate
    from another_brain.services.sql.profile import register_profile
    from another_brain.services.sql.repository import SQLiteMemoryRepository

    config.ensure_directories()
    factory = SQLiteConnectionFactory(config.database_path)
    factory.bootstrap()
    migrate(config.database_path)
    register_profile(factory)
    return MemoryService(
        repository=SQLiteMemoryRepository(factory, brain_id=config.brain_id),
        retriever=HybridMemoryRetriever(factory, brain_id=config.brain_id),
        audit=SQLiteAuditRepository(factory, brain_id=config.brain_id),
        embedder=ONNXEmbeddingProvider(profile_dir(config.model_cache_dir)),
        budgets=_NullBudgets(),
        storage=SQLiteHealthProbe(factory),
        config=config,
    )


def _cmd_recent(args: argparse.Namespace, config: AppConfig) -> int:
    """Print the bound brain newest-first on stdout; never loads the model."""
    records = _open_store(config).recent(limit=args.limit)
    if not records:
        print("no memories in this brain yet")
        return EXIT_OK
    for record in records:
        print(_recent_line(record))
    return EXIT_OK


def _recent_line(record: "MemoryRecord") -> str:
    """One recent line: diary day, id, topic, catalog, importance, summary.

    ``content`` never leaves the store — the line is a decision surface,
    not a detail dump. The summary is collapsed to a single line and
    truncated at 120 characters.
    """
    summary = " ".join(record.summary.split())
    if len(summary) > 120:
        summary = summary[:117] + "..."
    return (
        f"{record.timeline_day}  {record.memory_id}  "
        f"[{record.catalog}]  importance={record.importance}  "
        f"{record.topic}: {summary}"
    )


def _cmd_admin_restore(args: argparse.Namespace, config: AppConfig) -> int:
    """Undo a forget inside its grace window; prints the re-armed expiry.

    ``not_found`` (unknown/cross-brain/live/out-of-grace) is an honest
    stderr message with ``EXIT_ERROR``, mirroring ``import-jsonl``.
    """
    from datetime import datetime, timezone

    record = _open_store(config).restore(args.memory_id, agent_id="cli-admin")
    if record is None:
        _err(f"memory {args.memory_id!r}: not_found")
        return EXIT_ERROR
    iso = datetime.fromtimestamp(
        record.expires_at_ms / 1000, tz=timezone.utc
    ).isoformat()
    print(f"restored {record.memory_id}: expires {iso}")
    return EXIT_OK


def _cmd_admin_hard_delete(args: argparse.Namespace, config: AppConfig) -> int:
    """Permanently remove a memory; the audit trail survives."""
    if not _open_store(config).hard_delete(args.memory_id, agent_id="cli-admin"):
        _err(f"memory {args.memory_id!r}: not_found")
        return EXIT_ERROR
    print(f"hard-deleted {args.memory_id}")
    return EXIT_OK


def _cmd_model_status(config: AppConfig) -> int:
    """Install state from files on disk; never loads the model (TASK-046)."""
    from another_brain.services.embedding.model_installer import profile_dir, verify
    from another_brain.services.embedding.model_manifest import MODEL_MANIFEST

    states = verify(config.model_cache_dir)
    print(f"profile: {MODEL_MANIFEST.profile}")
    print(f"revision: {MODEL_MANIFEST.revision}")
    print(f"directory: {profile_dir(config.model_cache_dir)}")
    print(f"installed: {'yes' if all(s == 'ok' for s in states.values()) else 'no'}")
    for name, state in states.items():
        print(f"  {name}: {state}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
