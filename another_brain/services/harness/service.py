"""Harness connector service (TASK-093): cross-platform setup of the
another-brain MCP server + skill for each agent harness, from the installed
tool alone.

The registry rows live in ``harnesses.yaml`` (loaded by :mod:`.registry`);
this module is the behavior layer. All harness paths derive from an
injectable home directory (``pathlib.Path.home()`` by default) — the
``~/.<name>`` dotdirs are identical on Linux, macOS, and Windows, so the same
code serves every OS. No repo clone and no Node/npx are needed: the skill is
bundled inside the installed wheel and read via :mod:`importlib.resources`.

Registration is STDIO everywhere — the payload is the ``mcpServers`` entry
``{"command": "another-brain"}`` — never the old HTTP url form: the runtime is
zero-server, and the bare ``another-brain`` command is always the MCP stdio
server. Harnesses with a native CLI (claude, codex) get their config through
that CLI; the rest get a JSON upsert of their well-known config file. If the
CLI is absent we return a ``manual`` result carrying the exact snippet to
paste — in that config file's own language, JSON or TOML — never an
exception. Only the ``pi`` row overrides the payload: its adapter would
otherwise expose the tools as ``another_brain_brain_*``, so it writes
``toolPrefix: "none"`` and the bare ``brain_*`` wire names stay visible to
the agent.

Subprocess calls go through an injectable runner so tests never shell out;
defaults to :func:`subprocess.run` with the caller's environment.
"""
from __future__ import annotations

import importlib.resources
import json
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from another_brain.errors import BrainError
from another_brain.services.harness.registry import (
    MANUAL_JSON,
    SERVER_ENTRY,
    SERVER_NAME,
    Harness,
    HarnessResult,
    _BY_NAME,
    _HARNESSES,
)

SKILL_NAME = "another-brain"
SKILL_RESOURCE_DIR = "another_brain.skill"
SERVER_ENTRY_JSON = json.dumps(SERVER_ENTRY)
"""The bare entry value, as written into a JSON config's ``mcpServers``."""

# Pi's MCP adapter prefixes exposed tool names with the server name by
# default, turning ``brain_search`` into ``another_brain_brain_search``.
# ``toolPrefix: "none"`` keeps the wire ``brain_*`` names. Pi-specific: every
# other harness registers the standard SERVER_ENTRY payload unchanged.
PI_ENTRY: dict[str, object] = {**SERVER_ENTRY, "toolPrefix": "none"}

# The bundled skill is force-included into the wheel as another_brain/skill/
# (TASK-093). In the source tree that directory does not exist — the single
# editable copy lives at repo-root skills/another-brain/ — so when the
# resource package is not importable (running from a checkout) we fall back
# to the source-tree path. The wheel path stays primary; it is what the wheel
# gate proves.
_SKILL_FALLBACK = (
    Path(__file__).resolve().parents[3] / "skills" / "another-brain" / "SKILL.md"
)


def _bundled_skill() -> bytes:
    """Read the bundled SKILL.md: wheel resources first, source tree fallback."""
    try:
        return (
            importlib.resources.files(SKILL_RESOURCE_DIR)
            .joinpath("SKILL.md")
            .read_bytes()
        )
    except (ModuleNotFoundError, FileNotFoundError, OSError):
        return _SKILL_FALLBACK.read_bytes()


class UnknownHarnessError(BrainError):
    """A requested harness is not in the registry."""


# A runner is any callable with the signature of ``subprocess.run``. It is
# injected into :func:`connect` so tests can record invocations instead of
# shelling out; defaults to the real subprocess.
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def known_harnesses() -> tuple[str, ...]:
    """All registry harness names, in registry order."""
    return tuple(h.name for h in _HARNESSES)


def detect_harnesses(home: Path | None = None) -> tuple[str, ...]:
    """Detected harness names: those whose dotdir exists under ``home``."""
    root = home if home is not None else Path.home()
    return tuple(h.name for h in _HARNESSES if (root / h.detect_dir).is_dir())


def _home(home: Path | None) -> Path:
    return home if home is not None else Path.home()


def _runner_default(cmd, **kwargs) -> "subprocess.CompletedProcess[str]":
    """Default runner: ``subprocess.run`` with text output and no shell."""
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("check", False)
    return subprocess.run(cmd, **kwargs)


def _read_json(path: Path) -> dict:
    """Read the file as JSON; any invalid content starts from ``{}``."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    """Write preserving key insertion order, trailing newline; parents created."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def upsert_server(path: Path, entry: dict[str, object] | None = None) -> str:
    """Idempotently upsert the ``mcpServers`` entry into a JSON config file.

    ``entry`` replaces the whole payload, upgrading any previous bare entry
    in place; when omitted the standard :data:`SERVER_ENTRY` is written.
    Preserves every other key and server; returns a one-line status message.
    """
    server_entry = dict(SERVER_ENTRY) if entry is None else dict(entry)
    data = _read_json(path)
    data.setdefault("mcpServers", {})
    data["mcpServers"][SERVER_NAME] = server_entry
    _write_json(path, data)
    return (
        f"wrote {path}: mcpServers.{SERVER_NAME} = {json.dumps(server_entry)}"
    )


def install_skill(home: Path, skill_dir: str) -> tuple[bool, str]:
    """Install the wheel-bundled skill into ``<skill_dir>/another-brain``.

    Any pre-existing target directory is removed first so re-runs never nest
    (``another-brain/another-brain/...``). An unwritable target degrades to
    ``(False, message)`` — like the manual registration path, this never
    raises. Returns ``(ok, target_path_or_error)``.
    """
    target = _home(home) / skill_dir / SKILL_NAME
    try:
        if target.is_dir():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        target.joinpath("SKILL.md").write_bytes(_bundled_skill())
    except OSError as exc:
        return False, f"could not install the skill for {target}: {exc}"
    return True, str(target)


def connect(
    names: list[str],
    home: Path | None = None,
    runner: Runner | None = None,
) -> list[HarnessResult]:
    """Connect each harness: register the MCP server and install the skill.

    Raises :class:`UnknownHarnessError` on the first unknown name. Subprocess
    calls go through ``runner`` (defaults to :func:`_runner_default`) so
    tests never shell out.
    """
    run = runner if runner is not None else _runner_default
    root = _home(home)
    results: list[HarnessResult] = []
    for name in names:
        harness = _BY_NAME.get(name)
        if harness is None:
            raise UnknownHarnessError(
                f"unknown harness {name!r} — known: {', '.join(known_harnesses())}"
            )
        results.append(_connect_one(harness, root, run))
    return results


def _connect_one(
    harness: Harness, root: Path, run: Runner
) -> HarnessResult:
    messages: list[str] = []
    if harness.cli is not None and shutil.which(harness.cli):
        registered, message = _register_cli(harness, run)
        messages.append(message)
    elif harness.cli is not None:
        # CLI absent: return the manual-instruction result, never raise.
        registered, message = "manual", (
            f"{harness.cli} CLI not found — register {harness.name} by hand:"
            f" add to ~/{harness.mcp_file}:\n{harness.manual_snippet}"
        )
        messages.append(message)
    else:
        registered, message = "json", upsert_server(
            root / harness.mcp_file, harness.server_entry
        )
        messages.append(message)

    skill_ok, skill_path = install_skill(root, harness.skill_dir)
    if skill_ok:
        messages.append(f"installed the skill for {harness.name} -> {skill_path}")
    else:
        messages.append(skill_path)  # the failure message
        if registered != "manual":
            registered = "manual"  # surface as not-fully-done to the CLI
    return HarnessResult(
        name=harness.name,
        registered=registered,
        snippet=harness.manual_snippet,
        skill_installed=skill_ok,
        skill_path=skill_path,
        messages=messages,
    )


def _register_cli(harness: Harness, run: Runner) -> tuple[str, str]:
    """Register via the harness's native CLI, STDIO form.

    The claude CLI reports an existing user-scope entry as a confirmation on
    stdout with exit 0 (not an error), so the "already exists" gate is
    message-text based, and the response is remove + re-add so a changed
    config actually lands. ``codex mcp add`` overwrites idempotently
    and needs no re-add.
    """
    if harness.cli == "claude":
        add = [harness.cli, "mcp", "add", "-s", "user", "--transport", "stdio",
               SERVER_NAME, "--", SERVER_NAME]
        proc = run(add)
        if "already exists" in (proc.stdout or ""):
            run([harness.cli, "mcp", "remove", SERVER_NAME, "-s", "user"])
            proc = run(add)
        if proc.returncode != 0:
            raise RuntimeError(
                f"could not register {harness.name}: "
                f"`{' '.join(add)}` failed: {(proc.stderr or proc.stdout or '').strip()}"
            )
        return "cli", f"{harness.name}: registered stdio -> command {SERVER_NAME}"
    if harness.cli == "codex":
        add = [harness.cli, "mcp", "add", SERVER_NAME, "--", SERVER_NAME]
        proc = run(add)
        if proc.returncode != 0:
            raise RuntimeError(
                f"could not register {harness.name}: "
                f"`{' '.join(add)}` failed: {(proc.stderr or proc.stdout or '').strip()}"
            )
        return "cli", f"{harness.name}: registered stdio -> command {SERVER_NAME}"
    raise RuntimeError(f"no CLI registration path for {harness.name}")
