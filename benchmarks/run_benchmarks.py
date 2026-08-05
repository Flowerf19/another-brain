"""TASK-006 benchmark runner: sqlite-vec scalar `vec_distance_cosine` vs forced
NumPy fallback vs weighted FTS5 BM25, on the deterministic TASK-005 stores.

Protocol (Plan 07 evidence contract): 100 warmups + 1000 measured invocations
per store size/mode, 5 deterministic repetitions, raw samples retained, pooled
plus per-run p50/p95/p99. Parity: vec and NumPy must return identical
candidate IDs/order with |score diff| <= 1e-6 after canonical micro-cosine
(half-even round(score*1e6), floor 300000, key DESC then memory_id ASC).

Run from the repo root:  uv run python benchmarks/run_benchmarks.py [options]
Output:  benchmarks/evidence/benchmark-<run>.json + raw samples

Load control: the full locked protocol (100 warmups, 1000 measured, 5 runs)
is heavy sustained CPU (~30+ min). Use --measured/--runs/--sleep-ms for
reduced diagnostic runs; full-protocol evidence is produced on the reference
machine at the TASK-063 retrieval gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from importlib.metadata import version as pkg_version
from pathlib import Path

import numpy as np
import sqlite_vec

ROOT = Path(__file__).resolve().parents[1]
SPIKE = ROOT / "spikes" / "fp32"
STORES = ROOT / "benchmarks" / "stores"
EVIDENCE = ROOT / "benchmarks" / "evidence"

SEED = 20260804
NOW_MS = 1_785_000_000_000
BRAIN_ID = "bench-brain"
SIZES = [1_000, 10_000, 50_000, 100_000]
N_QUERIES = 50
WARMUPS = 100
MEASURED = 1000
RUNS = 5
CANDIDATE_LIMIT = 50
FLOOR_MICRO = 300_000

# Vector budgets from Plan 07 success criterion 9 (sqlite-vec path).
BUDGETS_P95_MS = {10_000: 25.0, 50_000: 75.0, 100_000: 150.0}

WHERE_LIVE = "brain_id = ? AND expires_at_ms > ? AND deleted_at_ms IS NULL"

TERM_RE = re.compile(r"[^\w]+", re.UNICODE)


def safe_terms(text: str) -> list[str]:
    return [t for t in TERM_RE.split(text.lower()) if t]


def micro_cosine(score: float) -> int:
    return round(float(score) * 1_000_000)  # Python half-even


def vec_candidates(con, query: bytes) -> list[tuple[str, int]]:
    rows = con.execute(
        f"SELECT memory_id, vec_distance_cosine(embedding, ?) AS d"
        f" FROM memories WHERE {WHERE_LIVE} ORDER BY d ASC, memory_id ASC"
        f" LIMIT {CANDIDATE_LIMIT}",
        (query, BRAIN_ID, NOW_MS),
    ).fetchall()
    out = []
    for memory_id, dist in rows:
        key = micro_cosine(1.0 - dist)
        if key >= FLOOR_MICRO:
            out.append((memory_id, key))
    out.sort(key=lambda r: (-r[1], r[0]))
    return out


def numpy_candidates(con, query: np.ndarray) -> list[tuple[str, int]]:
    rows = con.execute(
        f"SELECT memory_id, embedding FROM memories WHERE {WHERE_LIVE}",
        (BRAIN_ID, NOW_MS),
    ).fetchall()
    ids = [r[0] for r in rows]
    matrix = np.frombuffer(b"".join(r[1] for r in rows), dtype="<f4").reshape(len(rows), 640)
    norms = np.linalg.norm(matrix, axis=1)
    cos = (matrix @ query) / np.maximum(norms * np.linalg.norm(query), 1e-12)
    out = []
    for i, score in enumerate(cos):
        if not np.isfinite(score):
            continue
        key = micro_cosine(score)
        if key >= FLOOR_MICRO:
            out.append((ids[i], key))
    out.sort(key=lambda r: (-r[1], r[0]))
    return out[:CANDIDATE_LIMIT]


def fts_candidates(con, terms: list[str]) -> list[str]:
    match = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
    rows = con.execute(
        "SELECT m.memory_id FROM memory_fts f JOIN memories m ON m.row_id = f.rowid"
        f" WHERE f.memory_fts MATCH ? AND {WHERE_LIVE.replace('brain_id', 'm.brain_id').replace('expires_at_ms', 'm.expires_at_ms').replace('deleted_at_ms', 'm.deleted_at_ms')}"
        " ORDER BY bm25(memory_fts, 5.0, 3.0, 1.0) ASC, m.memory_id ASC"
        f" LIMIT {CANDIDATE_LIMIT}",
        (match, BRAIN_ID, NOW_MS),
    ).fetchall()
    return [r[0] for r in rows]


def timed(fn, samples: list[float]) -> None:
    start = time.perf_counter()
    fn()
    samples.append((time.perf_counter() - start) * 1000.0)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stats(samples: list[float]) -> dict:
    arr = np.array(samples)
    return {
        "n": len(samples),
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="*", default=SIZES)
    parser.add_argument("--warmups", type=int, default=WARMUPS)
    parser.add_argument("--measured", type=int, default=MEASURED)
    parser.add_argument("--runs", type=int, default=RUNS)
    parser.add_argument("--sleep-ms", type=float, default=0.0,
                        help="pause between measured calls (thermal pacing)")
    args = parser.parse_args()

    corpus = json.loads((SPIKE / "corpus" / "embedding-quality-v1.json").read_text(encoding="utf-8"))
    fts_texts = [q["text"] for q in corpus["queries"]][:N_QUERIES]

    run_id = datetime.now(timezone.utc).strftime("bench-%Y%m%dT%H%M%SZ")
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "utc": datetime.now(timezone.utc).isoformat(),
        "command": "uv run python benchmarks/run_benchmarks.py",
        "git": {"commit": __import__("subprocess").run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()},
        "versions": {
            "sqlite": sqlite3.sqlite_version,
            "sqlite_vec": pkg_version("sqlite-vec"),
            "numpy": np.__version__,
            "python": __import__("platform").python_version(),
        },
        "reference_machine_sha256": (ROOT / "benchmarks" / "reference-machine.json.sha256")
        .read_text().split()[0],
        "protocol": {
            "warmups": args.warmups, "measured": args.measured, "runs": args.runs,
            "queries_per_mode": N_QUERIES, "candidate_limit": CANDIDATE_LIMIT,
            "sleep_ms_between_calls": args.sleep_ms,
            "filters": "brain_id + live (expires_at_ms > now, deleted_at IS NULL); no collection narrowing (worst-case scan)",
            "micro_cosine": "half-even round(score*1e6), floor 300000, key DESC then memory_id ASC",
        },
        "stores": {},
        "results": {},
        "parity": {},
        "thresholds": {},
    }

    raw: dict[str, list[float]] = {}
    all_pass = True

    for size in args.sizes:
        path = STORES / f"brain-bench-{size}.sqlite3"
        manifest["stores"][str(size)] = {
            "path": path.name, "seed": SEED + size, "sha256": sha256(path),
            "size_mb": round(path.stat().st_size / 1e6, 1),
        }
        con = sqlite3.connect(path)
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
        con.execute("PRAGMA busy_timeout=5000")

        # deterministic vector queries: embeddings of 50 seeded live docs
        qrows = con.execute(
            f"SELECT embedding FROM memories WHERE {WHERE_LIVE}"
            " ORDER BY memory_id LIMIT ?",
            (BRAIN_ID, NOW_MS, N_QUERIES),
        ).fetchall()
        qvecs = [np.frombuffer(r[0], dtype="<f4").copy() for r in qrows]
        qbytes = [v.tobytes() for v in qvecs]

        # --- parity (once per store) ---
        mismatches = 0
        max_diff = 0.0
        for v, vb in zip(qvecs, qbytes):
            a = vec_candidates(con, vb)
            b = numpy_candidates(con, v)
            if [x[0] for x in a] != [x[0] for x in b]:
                mismatches += 1
            for (_, ka), (_, kb) in zip(a, b):
                max_diff = max(max_diff, abs(ka - kb) / 1e6)
        parity_ok = mismatches == 0 and max_diff <= 1e-6
        manifest["parity"][str(size)] = {
            "queries": N_QUERIES, "order_mismatches": mismatches,
            "max_score_diff": max_diff, "pass": parity_ok,
        }
        all_pass &= parity_ok

        # --- latency runs ---
        manifest["results"][str(size)] = {}
        rng = np.random.default_rng(SEED + size)
        for mode in ("vec", "numpy", "fts"):
            per_run = []
            pooled: list[float] = []
            for _run in range(args.runs):
                samples: list[float] = []
                for i in range(args.warmups):
                    j = i % N_QUERIES
                    if mode == "vec":
                        vec_candidates(con, qbytes[j])
                    elif mode == "numpy":
                        numpy_candidates(con, qvecs[j])
                    else:
                        fts_candidates(con, safe_terms(fts_texts[j]))
                for i in range(args.measured):
                    j = int(rng.integers(N_QUERIES))
                    if args.sleep_ms:
                        time.sleep(args.sleep_ms / 1000.0)
                    if mode == "vec":
                        timed(lambda: vec_candidates(con, qbytes[j]), samples)
                    elif mode == "numpy":
                        timed(lambda: numpy_candidates(con, qvecs[j]), samples)
                    else:
                        timed(lambda: fts_candidates(con, safe_terms(fts_texts[j])), samples)
                per_run.append(stats(samples))
                pooled.extend(samples)
                raw[f"{size}:{mode}:run{len(per_run)}"] = samples
            manifest["results"][str(size)][mode] = {
                "pooled": stats(pooled), "per_run": per_run,
            }
            print(f"{size:>7} {mode:>5}: p95={stats(pooled)['p95']:.1f}ms", flush=True)
        con.close()

        budget = BUDGETS_P95_MS.get(size)
        if budget is not None:
            vec_p95 = manifest["results"][str(size)]["vec"]["pooled"]["p95"]
            ok = vec_p95 <= budget
            manifest["thresholds"][f"vec_p95_{size}"] = {
                "value": vec_p95, "threshold": f"<= {budget}", "pass": ok,
            }
            all_pass &= ok

    manifest["overall_pass"] = bool(all_pass)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / f"{run_id}-samples.json").write_text(json.dumps(raw), encoding="utf-8")
    out = EVIDENCE / f"{run_id}.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\ngate: {'PASS' if all_pass else 'FAIL'} -> {out}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
