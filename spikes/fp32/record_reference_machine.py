"""Record the x86_64 reference machine manifest (part of TASK-005).

Must exist and be checksummed BEFORE any performance evidence run; later
performance manifests must match this hash or be an explicitly approved
replacement. Output: benchmarks/reference-machine.json + .sha256
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "benchmarks" / "reference-machine.json"


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def main() -> int:
    record = {
        "schema_version": 1,
        "recorded_by": "spikes/fp32/record_reference_machine.py",
        "os": {"system": platform.system(), "release": platform.release(),
               "version": platform.version()},
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "cpu": {
            "model": _cpu_model(),
            "logical_cores": os.cpu_count(),
            "physical_cores": len({
                tuple(p.split(":")[1].strip() for p in part.splitlines()
                      if p.startswith(("physical id", "core id")))
                for part in Path("/proc/cpuinfo").read_text().split("\n\n")
            }) if platform.system() == "Linux" else None,
        },
        "ram": {
            "mem_total_kb": int(_read("/proc/meminfo").split()[1])
            if _read("/proc/meminfo") else None,
        },
        "power": {
            "scaling_governor_cpu0": _read(
                "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
        },
        "python": {"version": platform.python_version(),
                   "implementation": platform.python_implementation()},
        "ort_thread_policy": "onnxruntime defaults (intra_op=num physical cores, inter_op=1); no affinity pinning",
        "cache_reset_procedure": "cold load = fresh process; no OS page-cache drop (requires root); warmups discard first-run effects",
        "notes": "First approved reference machine for Plan 07 performance evidence (TASK-005).",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record, indent=2, sort_keys=True) + "\n"
    OUT.write_text(payload, encoding="utf-8")
    sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    OUT.with_suffix(".json.sha256").write_text(f"{sha}  {OUT.name}\n", encoding="utf-8")
    print(payload)
    print(f"sha256: {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
