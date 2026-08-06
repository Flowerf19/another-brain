"""TASK-092: platform probe — every support-tier row of the measured wheel
matrix, plus libc mapping, case-insensitive machine spellings, and a
current_system() smoke test on the actual host.

detect_system is pure over injected strings, so the full tier table is
exercised with no host probing; only the smoke test touches the real
platform.
"""
from __future__ import annotations

import pytest

from another_brain.services.system import (
    SystemReport,
    current_system,
    detect_system,
)

# Every row of the tier table (plan 07.10), with the platform.machine()
# spellings each OS actually reports. machine is matched case-insensitively.
# sys_platform is a real sys.platform-style string; os_family is the expected
# family verdict.
TIER_CASES = [
    # sys_platform, machine, libc, macos_version, os_family, tier, expect_sqlite_vec, reason
    # -- windows --
    ("win32", "AMD64", "none", "", "windows", "supported", True, "CI-gated platform"),
    ("win32", "amd64", "none", "", "windows", "supported", True, "CI-gated platform"),
    (
        "win32",
        "ARM64",
        "none",
        "",
        "windows",
        "best_effort",
        False,
        "NumPy fallback; sqlite-vec excluded by dependency marker",
    ),
    (
        "win32",
        "arm64",
        "none",
        "",
        "windows",
        "best_effort",
        False,
        "NumPy fallback; sqlite-vec excluded by dependency marker",
    ),
    ("win32", "x86", "none", "", "windows", "unsupported", False, "untested platform"),
    # -- macos --
    ("darwin", "arm64", "none", "14.6.1", "macos", "supported", True, "CI-gated platform"),
    ("darwin", "ARM64", "none", "15.0", "macos", "supported", True, "CI-gated platform"),
    # boundary: major 13 is unsupported, major 14 is supported
    (
        "darwin",
        "arm64",
        "none",
        "13.7.1",
        "macos",
        "unsupported",
        True,
        "onnxruntime 1.28 requires macOS 14+",
    ),
    ("darwin", "arm64", "none", "14.0", "macos", "supported", True, "CI-gated platform"),
    # unknown macOS version is treated as unsupported
    ("darwin", "arm64", "none", "", "macos", "unsupported", True, "untested platform"),
    (
        "darwin",
        "x86_64",
        "none",
        "14.6.1",
        "macos",
        "unsupported",
        True,  # sqlite-vec ships a macOS x86_64 wheel; onnxruntime is the blocker
        "onnxruntime >=1.28 ships no macOS Intel wheel",
    ),
    (
        "darwin",
        "X86_64",
        "none",
        "14.6.1",
        "macos",
        "unsupported",
        True,
        "onnxruntime >=1.28 ships no macOS Intel wheel",
    ),
    ("darwin", "ppc64", "none", "14.0", "macos", "unsupported", False, "untested platform"),
    # -- linux --
    (
        "linux",
        "x86_64",
        "musl",
        "",
        "linux",
        "uninstallable",
        False,
        "sqlite-vec and onnxruntime ship no musl wheels; install fails at resolution",
    ),
    (
        "linux",
        "aarch64",
        "musl",
        "",
        "linux",
        "uninstallable",
        False,
        "sqlite-vec and onnxruntime ship no musl wheels; install fails at resolution",
    ),
    ("linux", "x86_64", "glibc", "", "linux", "supported", True, "CI-gated platform"),
    (
        "linux",
        "aarch64",
        "glibc",
        "",
        "linux",
        "best_effort",
        True,
        "resolves with full vector path; no CI coverage",
    ),
    ("linux", "AMD64", "glibc", "", "linux", "supported", True, "CI-gated platform"),
    # linux with an unknown libc falls back to unsupported
    ("linux", "x86_64", "", "", "linux", "unsupported", True, "untested platform"),
    # -- other --
    ("freebsd", "amd64", "none", "", "other", "unsupported", False, "untested platform"),
]


@pytest.mark.parametrize(
    "sys_platform, machine, libc, macos_version, os_family, tier, expect_vec, reason",
    TIER_CASES,
    ids=[
        f"{sys_platform}-{machine}-{libc or 'no-libc'}-macos{macos_version or '-'}"
        for sys_platform, machine, libc, macos_version, *_ in TIER_CASES
    ],
)
def test_tier_table(
    sys_platform, machine, libc, macos_version, os_family, tier, expect_vec, reason
):
    report = detect_system(
        sys_platform=sys_platform,
        machine=machine,
        libc_name=libc,
        macos_version=macos_version,
        python_version="3.12.0",
    )
    assert isinstance(report, SystemReport)
    assert report.os_family == os_family
    assert report.arch == machine  # raw, unmodified
    assert report.tier == tier
    assert report.expect_sqlite_vec == expect_vec
    assert report.reason == reason
    assert report.python_version == "3.12.0"


class TestLibcMapping:
    def test_linux_libc_name_case_insensitive(self):
        assert (
            detect_system(
                sys_platform="linux", machine="x86_64", libc_name="Glibc"
            ).libc
            == "glibc"
        )

    def test_linux_empty_libc_is_unknown(self):
        assert (
            detect_system(
                sys_platform="linux", machine="x86_64", libc_name=""
            ).libc
            == "unknown"
        )

    def test_linux_bogus_libc_is_unknown(self):
        assert (
            detect_system(
                sys_platform="linux", machine="x86_64", libc_name="bionic"
            ).libc
            == "unknown"
        )

    def test_non_linux_libc_is_none(self):
        for sys_platform in ("darwin", "win32", "freebsd"):
            assert (
                detect_system(
                    sys_platform=sys_platform,
                    machine="x86_64",
                    libc_name="glibc",
                ).libc
                == "none"
            )


class TestMacosBoundary:
    @pytest.mark.parametrize("version", ["14.0", "14.6.1", "15.0", "26.0"])
    def test_macos_14_and_newer_supported(self, version):
        report = detect_system(
            sys_platform="darwin", machine="arm64", libc_name="", macos_version=version
        )
        assert report.tier == "supported"
        assert report.expect_sqlite_vec is True
        assert report.macos_version == version

    @pytest.mark.parametrize("version", ["13.9", "13.0", "10.15", "12.6"])
    def test_macos_below_14_unsupported(self, version):
        report = detect_system(
            sys_platform="darwin", machine="arm64", libc_name="", macos_version=version
        )
        assert report.tier == "unsupported"
        assert report.reason == "onnxruntime 1.28 requires macOS 14+"
        assert report.expect_sqlite_vec is True  # vec still expected on arm64

    def test_macos_empty_version_unsupported(self):
        report = detect_system(
            sys_platform="darwin", machine="arm64", libc_name="", macos_version=""
        )
        assert report.tier == "unsupported"
        assert report.expect_sqlite_vec is True


class TestExpectSqliteVec:
    def test_windows_arm64_no_vec(self):
        report = detect_system(
            sys_platform="win32", machine="ARM64", libc_name="", macos_version=""
        )
        assert report.expect_sqlite_vec is False

    def test_windows_x64_vec(self):
        report = detect_system(
            sys_platform="win32", machine="AMD64", libc_name="", macos_version=""
        )
        assert report.expect_sqlite_vec is True

    def test_macos_intel_vec(self):
        # sqlite-vec 0.1.9 ships a macOS x86_64 wheel, so vec is expected
        # even though the tier is unsupported (onnxruntime is the blocker).
        report = detect_system(
            sys_platform="darwin", machine="x86_64", libc_name="", macos_version="14.0"
        )
        assert report.tier == "unsupported"
        assert report.expect_sqlite_vec is True

    def test_musl_no_vec(self):
        report = detect_system(
            sys_platform="linux", machine="x86_64", libc_name="musl", macos_version=""
        )
        assert report.expect_sqlite_vec is False

    def test_other_platform_no_vec(self):
        report = detect_system(
            sys_platform="freebsd", machine="amd64", libc_name="", macos_version=""
        )
        assert report.expect_sqlite_vec is False


class TestCurrentSystem:
    def test_host_is_supported_linux_x86_64_glibc(self):
        """This machine is linux x86_64 glibc (CI/developer host)."""
        report = current_system()
        assert report.os_family == "linux"
        assert report.tier == "supported"
        assert report.expect_sqlite_vec is True
        assert report.libc == "glibc"
        assert report.macos_version == ""
        assert report.python_version == __import__("platform").python_version()
