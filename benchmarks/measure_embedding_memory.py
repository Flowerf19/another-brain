"""Measure per-process embedding memory for the release metric (TASK-044).

Runs the PRODUCT provider (src) in this process — not the spike — against a
real installed profile directory:

    uv run python benchmarks/measure_embedding_memory.py --profile-dir DIR

DIR must be a completed q4 profile (marker + verified files; on this machine
`spikes/fp32/.models/q4` works with a symlinked profile + marker, see
`benchmarks/reports/embedding-memory-2026-08-04.md`).

Points: interpreter baseline → provider constructed (NOT_LOADED) → first
embed (session loaded) → warm embed → after close(). RSS/PSS come from
Linux `/proc/self/smaps_rollup`; the script refuses to run elsewhere.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from another_brain.services.embedding.provider import ONNXEmbeddingProvider  # noqa: E402
from another_brain.protocols import EmbeddingHealth  # noqa: E402


def _smaps() -> dict[str, int]:
    path = Path("/proc/self/smaps_rollup")
    if not path.exists():
        raise SystemExit("refusing to measure: /proc/self/smaps_rollup unavailable (non-Linux?)")
    rss = pss = 0
    for line in path.read_text().splitlines():
        if line.startswith("Rss:"):
            rss = int(line.split()[1])
        elif line.startswith("Pss:"):
            pss = int(line.split()[1])
    return {"rss_kib": rss, "pss_kib": pss}


def measure(profile_dir: Path) -> list[dict]:
    points = [("baseline", _smaps())]
    provider = ONNXEmbeddingProvider(profile_dir)
    assert provider.health() is EmbeddingHealth.NOT_LOADED
    points.append(("constructed", _smaps()))
    provider.embed_query("memory probe: sqlite fts5 vector benchmark")
    points.append(("loaded_first_embed", _smaps()))
    provider.embed_document(topic="memory-probe", summary="second call, warm session")
    points.append(("warm_embed", _smaps()))
    provider.close()
    points.append(("after_close", _smaps()))
    return [{"point": name, **mem} for name, mem in points]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-dir", required=True, type=Path)
    parser.add_argument("--json", type=Path, default=None, help="write raw JSON evidence")
    args = parser.parse_args()
    if not (args.profile_dir / ".installed.json").exists():
        raise SystemExit(f"not a completed profile: {args.profile_dir}")
    rows = measure(args.profile_dir)
    print(f"{'point':<22} {'RSS MiB':>9} {'PSS MiB':>9}")
    for row in rows:
        print(f"{row['point']:<22} {row['rss_kib'] / 1024:>9.1f} {row['pss_kib'] / 1024:>9.1f}")
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        print(f"evidence: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
