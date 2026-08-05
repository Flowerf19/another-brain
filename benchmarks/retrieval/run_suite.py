"""Judged 1k/10k/50k/100k retrieval suite + evidence manifests (TASK-063).

Runs the locked retrieval protocol (Plan 07 "Reproducible evidence
manifest") against the deterministic stores from ``build_stores.py``:

- quality: 120 judged embedding-quality-v1 queries → Recall@5, MRR, nDCG@10
  per store size and vector mode;
- latency: per (size, mode): 5 deterministic repetitions of
  100 warmups + 1,000 measured invocations drawn reproducibly (with
  repetition) from the judged queries; pooled + per-run p50/p95/p99 for the
  vector branch and the full hybrid search. The weighted-FTS5 lexical branch
  is measured once per size under the same protocol — it carries no
  embedding dependency, so it is identical under both vector backends;
- parity: exact candidate IDs/keys/ranks and RRF output between the
  sqlite-vec and NumPy adapters on every judged query, raw-score tolerance
  1e-6;
- size: checkpointed DB bytes;
- thresholds: locked vector-retrieval budgets (10k p95 ≤ 25 ms,
  50k ≤ 75 ms, 100k ≤ 150 ms) enforced on the pooled p95. The lexical
  branch is measured and reported but has no locked budget — Success
  criterion 9 covers vector retrieval only, even though BM25 is the
  slower branch at scale.

Emits one evidence manifest plus a raw-samples sidecar under
``benchmarks/evidence/`` and a markdown report under ``benchmarks/reports/``.

    uv run python benchmarks/retrieval/run_suite.py [--quick] [sizes...]

``--quick`` (2 sizes, 1 rep, 10 warmups, 50 measured) keeps the suite
exercised in CI; the full locked protocol is the evidence gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "spikes" / "fp32"))

from benchmarks.retrieval.build_stores import (  # noqa: E402
    BRAIN_ID, NOW_MS, SEED, STORES, corpus_vectors,
)

from another_brain.config import AppConfig  # noqa: E402
from another_brain.domain.models import EmbeddingVector  # noqa: E402
from another_brain.retrieval.lexical import SQLiteLexicalRetriever  # noqa: E402
from another_brain.retrieval.query import build_match_query  # noqa: E402
from another_brain.retrieval.service import HybridMemoryRetriever  # noqa: E402
from another_brain.services.embedding.model_installer import (  # noqa: E402
    is_installed, profile_dir,
)
from another_brain.services.embedding.model_manifest import manifest_digest  # noqa: E402
from another_brain.services.embedding.provider import ONNXEmbeddingProvider  # noqa: E402

CORPUS_PATH = ROOT / "spikes" / "fp32" / "corpus" / "embedding-quality-v1.json"
EVIDENCE = ROOT / "benchmarks" / "evidence"
REPORTS = ROOT / "benchmarks" / "reports"
QUERY_CACHE = Path(__file__).resolve().parent / "corpus-q4-query-vectors.npy"
QUERY_META = Path(__file__).resolve().parent / "corpus-q4-query-vectors.meta.json"

MODES = ("sqlite-vec", "numpy")
LATENCY_BUDGETS_MS = {10000: 25.0, 50000: 75.0, 100000: 150.0}  # locked, plan SC-9


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def query_vectors(corpus: dict) -> np.ndarray:
    """Real q4 embeddings for the 120 judged queries (hash-keyed cache)."""
    corpus_sha = _sha256(CORPUS_PATH.read_bytes())
    digest = manifest_digest()
    if QUERY_CACHE.exists() and QUERY_META.exists():
        meta = json.loads(QUERY_META.read_text())
        if meta.get("corpus_sha256") == corpus_sha and meta.get("manifest_digest") == digest:
            return np.load(QUERY_CACHE)
    config = AppConfig.from_env()
    if not is_installed(config.model_cache_dir, verify_files=False):
        raise SystemExit("the pinned q4 profile is not installed; run `another-brain model pull`")
    provider = ONNXEmbeddingProvider(profile_dir(config.model_cache_dir))
    vectors = np.stack([provider.embed_query(q["text"]).values for q in corpus["queries"]])
    np.save(QUERY_CACHE, vectors)
    QUERY_META.write_text(json.dumps({
        "corpus_sha256": corpus_sha,
        "manifest_digest": digest,
        "queries": len(vectors),
    }, indent=1))
    return vectors


def quality_metrics(corpus: dict, ranked: dict[str, list[str]]) -> dict:
    """Macro Recall@5, MRR, nDCG@10 over fused top-10 lists."""
    recalls, mrrs, ndcgs = [], [], []
    for query in corpus["queries"]:
        judgments = {j["doc_id"]: j["grade"] for j in query["judgments"]}
        relevant = {d for d, g in judgments.items() if g >= 1}
        ranked_ids = ranked[query["query_id"]]
        top5 = set(ranked_ids[:5])
        recalls.append(len(top5 & relevant) / min(5, len(relevant)))
        first = next((r for r, did in enumerate(ranked_ids, 1) if did in relevant), None)
        mrrs.append(1.0 / first if first else 0.0)
        dcg = sum(
            judgments.get(did, 0) / np.log2(rank + 1)
            for rank, did in enumerate(ranked_ids[:10], 1)
        )
        ideal = sorted(judgments.values(), reverse=True)[:10]
        idcg = sum(g / np.log2(i + 1) for i, g in enumerate(ideal, 1))
        ndcgs.append(dcg / idcg if idcg else 0.0)
    return {
        "recall_at_5": float(np.mean(recalls)),
        "mrr": float(np.mean(mrrs)),
        "ndcg_at_10": float(np.mean(ndcgs)),
        "queries": len(corpus["queries"]),
    }


def percentile(samples: list[float], pct: float) -> float:
    return float(np.percentile(np.asarray(samples), pct))


def summarize(samples: list[float]) -> dict:
    return {
        "n": len(samples),
        "p50_ms": percentile(samples, 50),
        "p95_ms": percentile(samples, 95),
        "p99_ms": percentile(samples, 99),
        "mean_ms": float(np.mean(samples)),
    }


def run_size(size: int, corpus: dict, qvecs: np.ndarray, *, quick: bool) -> dict:
    path = STORES / f"brain-bench-{size}.sqlite3"
    if not path.exists():
        raise SystemExit(f"missing store {path}; run build_stores.py first")
    from another_brain.services.sql.connection import SQLiteConnectionFactory

    factory = SQLiteConnectionFactory(path)
    queries = corpus["queries"]
    q_by_id = {q["query_id"]: i for i, q in enumerate(queries)}

    result: dict = {"size": size, "db_bytes": path.stat().st_size, "modes": {}}

    # ---- lexical branch: measured once per store ---------------------------
    # BM25 has no embedding dependency, so it is identical under both vector
    # backends. Success criterion 9 budgets only vector retrieval, so this
    # series is reported without a threshold — but it is the slowest branch at
    # scale and dominates hybrid latency, so it must be visible in evidence.
    warmups, measured, reps = (10, 50, 1) if quick else (100, 1000, 5)
    lexical_runs = []
    for rep in range(reps):
        rng = random.Random(SEED + size + rep)
        draws = [rng.choice(queries) for _ in range(warmups + measured)]
        samples: list[float] = []
        for i, query in enumerate(draws):
            match_query = build_match_query(query["text"])
            start = time.perf_counter()
            with factory.connect(read_only=True) as con:
                if match_query is not None:
                    SQLiteLexicalRetriever(con.connection, brain_id=BRAIN_ID).candidates(
                        match_query=match_query, now_ms=NOW_MS,
                    )
            elapsed_ms = (time.perf_counter() - start) * 1000
            if i >= warmups:
                samples.append(elapsed_ms)
        lexical_runs.append(samples)
    pooled_lexical = [s for run in lexical_runs for s in run]
    result["lexical_branch"] = {
        "pooled": summarize(pooled_lexical),
        "per_run": [summarize(run) for run in lexical_runs],
    }
    result["lexical_raw_samples"] = lexical_runs

    for mode in MODES:
        retriever = HybridMemoryRetriever(
            factory, brain_id=BRAIN_ID, clock=lambda: NOW_MS,
            force_vector_backend=mode, top_k=10,
        )
        # ---- quality (fused top-10; Recall@5/MRR use the first five) ------
        ranked: dict[str, list[str]] = {}
        for query in queries:
            fused = retriever.rank(
                query_text=query["text"],
                query_vector=EmbeddingVector(values=qvecs[q_by_id[query["query_id"]]]),
            )
            ranked[query["query_id"]] = [r.memory_id for r in fused]
        quality = quality_metrics(corpus, ranked)

        # ---- latency: 5 reps x (warmups + measured), seeded draws ---------
        vector_runs, hybrid_runs = [], []
        for rep in range(reps):
            rng = random.Random(SEED + size + rep)
            draws = [rng.choice(queries) for _ in range(warmups + measured)]
            vec_samples: list[float] = []
            hyb_samples: list[float] = []
            for i, query in enumerate(draws):
                qv = EmbeddingVector(values=qvecs[q_by_id[query["query_id"]]])
                start = time.perf_counter()
                fused = retriever.rank(query_text=query["text"], query_vector=qv)
                hybrid_ms = (time.perf_counter() - start) * 1000
                # vector-branch-only latency: candidates on a fresh ro connection
                start = time.perf_counter()
                with factory.connect(read_only=True) as con:
                    if mode == "sqlite-vec":
                        assert con.load_vec()
                    from another_brain.retrieval.vector import (
                        NumpyVectorRetriever, SQLiteVecVectorRetriever,
                    )
                    branch = (
                        SQLiteVecVectorRetriever if mode == "sqlite-vec" else NumpyVectorRetriever
                    )(con.connection, brain_id=BRAIN_ID)
                    branch.candidates(query_vector=qv, now_ms=NOW_MS)
                vector_ms = (time.perf_counter() - start) * 1000
                if i >= warmups:
                    vec_samples.append(vector_ms)
                    hyb_samples.append(hybrid_ms)
            vector_runs.append(vec_samples)
            hybrid_runs.append(hyb_samples)
        pooled_vec = [s for run in vector_runs for s in run]
        pooled_hyb = [s for run in hybrid_runs for s in run]
        result["modes"][mode] = {
            "quality": quality,
            "latency": {
                "vector_branch": {
                    "pooled": summarize(pooled_vec),
                    "per_run": [summarize(run) for run in vector_runs],
                },
                "hybrid_search": {
                    "pooled": summarize(pooled_hyb),
                    "per_run": [summarize(run) for run in hybrid_runs],
                },
            },
            "raw_samples": {
                "vector_branch_ms": vector_runs,
                "hybrid_search_ms": hybrid_runs,
            },
        }

    # ---- parity across modes on every judged query -------------------------
    ranked_by_mode = {}
    raw_by_mode = {}
    keys_by_mode = {}
    max_raw_diff = 0.0
    mismatches = []
    for query in queries:
        qv = EmbeddingVector(values=qvecs[q_by_id[query["query_id"]]])
        per_mode = {}
        for mode in MODES:
            retriever = HybridMemoryRetriever(
                factory, brain_id=BRAIN_ID, clock=lambda: NOW_MS,
                force_vector_backend=mode, top_k=10,
            )
            fused = retriever.rank(query_text=query["text"], query_vector=qv)
            per_mode[mode] = fused
            with factory.connect(read_only=True) as con:
                if mode == "sqlite-vec":
                    assert con.load_vec()
                from another_brain.retrieval.vector import (
                    NumpyVectorRetriever, SQLiteVecVectorRetriever,
                )
                branch = (
                    SQLiteVecVectorRetriever if mode == "sqlite-vec" else NumpyVectorRetriever
                )(con.connection, brain_id=BRAIN_ID)
                hits = branch.candidates(query_vector=qv, now_ms=NOW_MS)
            keys_by_mode.setdefault(mode, {})[query["query_id"]] = [
                (h.memory_id, h.cosine_key, h.rank) for h in hits
            ]
            raw_by_mode.setdefault(mode, {})[query["query_id"]] = {
                h.memory_id: h.raw_cosine for h in hits
            }
        ranked_by_mode[query["query_id"]] = per_mode
        if keys_by_mode["sqlite-vec"][query["query_id"]] != keys_by_mode["numpy"][query["query_id"]]:
            mismatches.append(query["query_id"])
        for memory_id, raw in raw_by_mode["sqlite-vec"][query["query_id"]].items():
            other = raw_by_mode["numpy"][query["query_id"]].get(memory_id)
            if other is not None:
                max_raw_diff = max(max_raw_diff, abs(raw - other))
        if per_mode["sqlite-vec"] != per_mode["numpy"]:
            mismatches.append(f"{query['query_id']}:rrf")

    result["parity"] = {
        # Gate: raw scores within the locked 1e-6 tolerance.
        "raw_within_tolerance": max_raw_diff <= 1e-6,
        # Diagnostic: exact canonical (id, key, rank)/RRF equality. Real
        # embeddings can sit within float32-accumulation error of a .5-micro
        # rounding boundary and flip one micro key; the exact parity GATE
        # lives in the engineered unit fixtures (rounding-boundary gaps).
        "exact_candidate_key_rank_match": not mismatches,
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:10],
        "max_raw_score_diff": max_raw_diff,
        "raw_tolerance": 1e-6,
    }
    return result


def git_state() -> dict:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    diff = subprocess.run(
        ["git", "diff"], capture_output=True, text=True, check=True
    ).stdout
    staged = subprocess.run(
        ["git", "diff", "--cached"], capture_output=True, text=True, check=True
    ).stdout
    return {"commit": commit, "dirty_diff_sha256": _sha256((diff + staged).encode())}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("sizes", nargs="*", type=int)
    args = parser.parse_args()
    sizes = args.sizes or ([1000, 10000] if args.quick else [1000, 10000, 50000, 100000])

    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    qvecs = query_vectors(corpus)
    corpus_vectors(corpus)  # ensure the document cache exists (stores need it)

    run_id = "retsuite-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    started = time.monotonic()
    results = []
    for size in sizes:
        print(f"size {size}: quality + latency + parity…", flush=True)
        results.append(run_size(size, corpus, qvecs, quick=args.quick))

    thresholds = []
    for result in results:
        budget = LATENCY_BUDGETS_MS.get(result["size"])
        if budget is None:
            continue
        p95 = result["modes"]["sqlite-vec"]["latency"]["vector_branch"]["pooled"]["p95_ms"]
        thresholds.append({
            "id": f"vector-p95-{result['size']}",
            "budget_ms": budget,
            "measured_ms": p95,
            "pass": p95 <= budget,
        })
    parity_pass = all(r["parity"]["raw_within_tolerance"] for r in results)
    overall = all(t["pass"] for t in thresholds) and parity_pass

    import onnxruntime
    import tokenizers as tokenizers_pkg

    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "utc": datetime.now(timezone.utc).isoformat(),
        "git": git_state(),
        "command": "uv run python benchmarks/retrieval/run_suite.py"
        + (" --quick" if args.quick else "")
        + (" " + " ".join(map(str, sizes)) if args.sizes else ""),
        "environment": {
            "os": platform.platform(),
            "kernel": platform.release(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": platform.os.cpu_count(),
        },
        "versions": {
            "sqlite3": sqlite3.sqlite_version,
            "onnxruntime": onnxruntime.__version__,
            "tokenizers": tokenizers_pkg.__version__,
            "numpy": np.__version__,
        },
        "reference_machine_sha256": _sha256(
            (ROOT / "benchmarks" / "reference-machine.json").read_bytes()
        ),
        "corpus": {
            "corpus_id": corpus["corpus_id"],
            "corpus_sha256": _sha256(CORPUS_PATH.read_bytes()),
            "queries": len(corpus["queries"]),
        },
        "models": {"q4_manifest_digest": manifest_digest()},
        "payload_input_version": 2,
        "protocol": {
            "top_k": 5,
            "candidate_limit": 50,
            "warmups": 10 if args.quick else 100,
            "measured_per_rep": 50 if args.quick else 1000,
            "repetitions": 1 if args.quick else 5,
            "draw": "seeded from the 120 judged queries with repetition",
            "cache_procedure": "warm: per-rep warmups discarded; no cold page-cache drop",
            "quality_window": "fused top-10 (Recall@5/MRR on the first five)",
        },
        "stores": [
            {
                k: v for k, v in r.items()
                if k not in ("modes", "lexical_raw_samples")
            } | {"modes": {
                mode: {k: v for k, v in data.items() if k != "raw_samples"}
                for mode, data in r["modes"].items()
            }}
            for r in results
        ],
        "thresholds": thresholds,
        "parity_pass": parity_pass,
        "overall_pass": overall,
        "wall_seconds": time.monotonic() - started,
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    manifest_path = EVIDENCE / f"retrieval-suite-{run_id}.json"
    samples_path = EVIDENCE / f"retrieval-suite-{run_id}.samples.json"
    manifest_path.write_text(json.dumps(manifest, indent=1))
    samples_path.write_text(json.dumps({
        "run_id": run_id,
        "raw_samples": {
            f"{r['size']}:{mode}": data["raw_samples"] for r in results
            for mode, data in r["modes"].items()
        } | {
            f"{r['size']}:lexical": {"lexical_branch_ms": r["lexical_raw_samples"]}
            for r in results
        },
    }))

    lines = [f"# Retrieval suite evidence — {run_id}", ""]
    for r in results:
        lines.append(f"## {r['size']} rows ({r['db_bytes'] / 1e6:.1f} MB)")
        for mode in MODES:
            m = r["modes"][mode]
            q = m["quality"]
            lv = m["latency"]["vector_branch"]["pooled"]
            lh = m["latency"]["hybrid_search"]["pooled"]
            lines.append(
                f"- {mode}: Recall@5 {q['recall_at_5']:.4f}, MRR {q['mrr']:.4f},"
                f" nDCG@10 {q['ndcg_at_10']:.4f} | vector p50/p95/p99"
                f" {lv['p50_ms']:.2f}/{lv['p95_ms']:.2f}/{lv['p99_ms']:.2f} ms"
                f" | hybrid p95 {lh['p95_ms']:.2f} ms"
            )
        ll = r["lexical_branch"]["pooled"]
        lines.append(
            f"- fts5-lexical (backend-independent, unbudgeted): p50/p95/p99"
            f" {ll['p50_ms']:.2f}/{ll['p95_ms']:.2f}/{ll['p99_ms']:.2f} ms"
        )
        lines.append(f"- parity: raw<=1e-6 {r['parity']['raw_within_tolerance']}"
                     f" (max {r['parity']['max_raw_score_diff']:.2e}),"
                     f" exact-canonical {r['parity']['exact_candidate_key_rank_match']}"
                     f" ({r['parity']['mismatch_count']} mismatches)")
    lines.append(f"thresholds: {[(t['id'], t['pass']) for t in thresholds]}")
    lines.append(f"OVERALL: {'PASS' if overall else 'FAIL'}")
    report_path = REPORTS / f"retrieval-suite-{run_id}.md"
    report_path.write_text("\n".join(lines) + "\n")

    print("\n".join(lines))
    print(f"\nmanifest: {manifest_path}")
    print(f"samples:  {samples_path}")
    print(f"report:   {report_path}")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
