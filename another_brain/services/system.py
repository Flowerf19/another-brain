"""Platform probe service (TASK-092): the single source of truth for
OS/arch/libc detection and the support-tier verdict.

The tier table encodes the *measured* dependency-wheel matrix (plan
07.10, measured 2026-08-06 from the PyPI file matrices) — onnxruntime
1.28 wheels, sqlite-vec 0.1.9 wheels, tokenizers, numpy. Every decision
lives in :func:`detect_system`, which is pure over injected strings, so
the verdict is unit-testable without touching the host. The product's
wheels are ``py3-none-any``; all platform risk is in the native
dependencies.

Pure standard library: no onnxruntime, sqlite-vec, or numpy import, so
this module is importable anywhere (including platforms where those
wheels do not resolve).

Feeds the ``another-brain doctor`` verdict line (TASK-084); no CLI
wiring here.
"""
from __future__ import annotations

import os
import platform
import re
import sys
from dataclasses import dataclass
from typing import Literal

OSFamily = Literal["linux", "macos", "windows", "other"]
Libc = Literal["glibc", "musl", "none", "unknown"]
Tier = Literal["supported", "best_effort", "uninstallable", "unsupported"]

_MACOS_MAJOR_RE = re.compile(r"^(\d+)")
# onnxruntime 1.28 ships macOS wheels only for macosx_14_0+ (Apple Silicon).
_MACOS_MIN_MAJOR = 14

_X86_64 = ("x86_64", "amd64")  # both spellings per OS (lowercased)
_ARM64 = ("aarch64", "arm64")  # Linux vs macOS/Windows spelling


@dataclass(frozen=True)
class SystemReport:
    """Immutable platform snapshot and support-tier verdict.

    ``arch`` is the raw :func:`platform.machine` string as reported;
    tier logic matches it case-insensitively, so ``AMD64`` and ``amd64``
    resolve identically.
    """

    os_family: OSFamily
    arch: str
    libc: Libc
    macos_version: str  # "" when not macOS
    python_version: str
    tier: Tier
    expect_sqlite_vec: bool  # whether sqlite-vec is expected on this platform
    reason: str


def _os_family(sys_platform: str) -> OSFamily:
    if sys_platform.startswith("linux"):
        return "linux"
    if sys_platform == "darwin":
        return "macos"
    if sys_platform.startswith("win32"):
        return "windows"
    return "other"


def _normalize(machine: str) -> str:
    """Lowercase platform.machine() for case-insensitive tier matching."""
    return machine.strip().lower()


def _is_x86_64(machine: str) -> bool:
    return _normalize(machine) in _X86_64


def _is_arm64(machine: str) -> bool:
    return _normalize(machine) in _ARM64


def _macos_major(macos_version: str) -> int | None:
    match = _MACOS_MAJOR_RE.match(macos_version.strip())
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _tier_for(
    os_family: OSFamily,
    machine: str,
    libc: Libc,
    macos_version: str,
) -> tuple[Tier, str]:
    """Support-tier verdict and reason per the measured wheel matrix.

    Matched case-insensitively: Windows reports ``AMD64``/``ARM64``,
    macOS Apple Silicon reports ``arm64``, Linux ARM reports ``aarch64``.
    """
    if os_family == "windows":
        if _is_x86_64(machine):
            return "supported", "CI-gated platform"
        if _is_arm64(machine):
            # sqlite-vec ships no win_arm64 wheel and is marker-excluded
            # there; onnxruntime does ship win_arm64.
            return "best_effort", "NumPy fallback; sqlite-vec excluded by dependency marker"
        return "unsupported", "untested platform"
    if os_family == "macos":
        if _is_arm64(machine):
            if _macos_major(macos_version) is None:
                return "unsupported", "untested platform"
            if _macos_major(macos_version) >= _MACOS_MIN_MAJOR:
                # onnxruntime 1.28 wheel is tagged macosx_14_0 (arm64 only).
                return "supported", "CI-gated platform"
            return "unsupported", "onnxruntime 1.28 requires macOS 14+"
        if _is_x86_64(machine):
            # onnxruntime >=1.28 ships no macOS Intel wheel.
            return "unsupported", "onnxruntime >=1.28 ships no macOS Intel wheel"
        return "unsupported", "untested platform"
    if os_family == "linux":
        if libc == "musl":
            return "uninstallable", "sqlite-vec and onnxruntime ship no musl wheels; install fails at resolution"
        if libc == "glibc" and _is_x86_64(machine):
            return "supported", "CI-gated platform"
        if libc == "glibc" and _is_arm64(machine):
            # sqlite-vec ships a manylinux aarch64 wheel: full vector path,
            # but no CI hardware proves it.
            return "best_effort", "resolves with full vector path; no CI coverage"
        return "unsupported", "untested platform"
    return "unsupported", "untested platform"


def detect_system(
    *,
    sys_platform: str,
    machine: str,
    libc_name: str,
    macos_version: str = "",
    python_version: str = "",
) -> SystemReport:
    """Pure platform verdict from injected inputs (no host probing).

    ``libc_name`` is the ``platform.libc_ver()`` libc name (lowercase
    "glibc"/"musl"), or "" when unknown. On macOS/Windows the libc is
    ``"none"`` regardless of input; on linux, an empty libc name falls
    back to ``"unknown"`` — :func:`current_system` resolves the
    Alpine/musl case before calling this.
    """
    os_family = _os_family(sys_platform)
    if os_family == "linux":
        libc: Libc = (
            "glibc" if libc_name.lower() == "glibc"
            else "musl" if libc_name.lower() == "musl"
            else "unknown"
        )
    else:
        libc = "none"
    tier, reason = _tier_for(os_family, machine, libc, macos_version)

    # sqlite-vec 0.1.9 wheel availability (no sdist), exactly: manylinux
    # x86_64/aarch64, macOS arm64, macOS x86_64, win_amd64.
    expect_sqlite_vec = (
        (os_family == "linux" and libc != "musl" and (_is_x86_64(machine) or _is_arm64(machine)))
        or (os_family == "macos" and (_is_arm64(machine) or _is_x86_64(machine)))
        or (os_family == "windows" and _is_x86_64(machine))
    )

    return SystemReport(
        os_family=os_family,
        arch=machine,
        libc=libc,
        macos_version=macos_version,
        python_version=python_version,
        tier=tier,
        expect_sqlite_vec=expect_sqlite_vec,
        reason=reason,
    )


def current_system() -> SystemReport:
    """Probe the actual host and return its platform report.

    libc detection: ``platform.libc_ver()`` on linux (treat an empty
    libc name with ``/etc/alpine-release`` present as musl — musl
    systems report no libc name); non-linux is ``"none"``. macOS
    version is best-effort from ``platform.mac_ver()`` and may be ""
    when unparseable, in which case the tier falls back to
    ``"unsupported"`` (mirroring :func:`detect_system`).
    """
    sys_platform = sys.platform.lower()
    machine = platform.machine()

    libc_name = ""
    if sys_platform.startswith("linux"):
        libc_name = platform.libc_ver()[0]
        if not libc_name and os.path.exists("/etc/alpine-release"):
            libc_name = "musl"

    macos_version = ""
    if sys_platform == "darwin":
        try:
            macos_version = platform.mac_ver()[0]
        except Exception:
            macos_version = ""

    return detect_system(
        sys_platform=sys_platform,
        machine=machine,
        libc_name=libc_name,
        macos_version=macos_version,
        python_version=platform.python_version(),
    )
