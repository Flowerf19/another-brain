"""TASK-003/004 evaluation runner: quality metrics, q4 resource measurement,
reproducible evidence manifest, and gate enforcement.

Run from the REPO ROOT environment:

    uv run python spikes/fp32/run_evaluation.py quality    # cosine + Recall@5/MRR/nDCG@10
    uv run python spikes/fp32/run_evaluation.py resources  # q4 latency/RSS/cold/PSS
    uv run python spikes/fp32/run_evaluation.py gate       # manifest + threshold verdict

`gate` merges the two result files into evidence/manifest-<run>.json and
exits non-zero if any locked threshold fails (TASK-004).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
EVIDENCE = SPIKE / "evidence"
sys.path.insert(0, str(SPIKE))

TOP_K_RECALL = 5
NDCG_K = 10

THRESHOLDS = {
    "paired_cosine_median": {"op": ">=", "value": 0.99},
    "paired_cosine_p5": {"op": ">=", "value": 0.97},
    "q4_recall_at_5": {"op": ">=", "value": 0.90},
    "q4_mrr": {"op": ">=", "value": 0.80},
    "q4_ndcg_at_10": {"op": ">=", "value": 0.85},
    "delta_recall_at_5": {"op": "<=", "value": 0.02},
    "delta_mrr": {"op": "<=", "value": 0.02},
    "delta_ndcg_at_10": {"op": "<=", "value": 0.02},
    "delta_lang_recall_at_5": {"op": "<=", "value": 0.03},
    "delta_lang_mrr": {"op": "<=", "value": 0.03},
    "delta_lang_ndcg_at_10": {"op": "<=", "value": 0.03},
    "warm_p95_ms_le_128_tokens": {"op": "<=", "value": 100.0},
    "steady_rss_mib": {"op": "<=", "value": 500.0},
}


# --------------------------------------------------------------------------
def _load():
    corpus = json.loads((SPIKE / "corpus" / "embedding-quality-v1.json").read_text(encoding="utf-8"))
    q4 = np.load(EVIDENCE / "q4_corpus.npy")
    fp32 = np.load(EVIDENCE / "fp32_corpus.npy")
    return corpus, q4, fp32


def _metrics_for(queries, scores: np.ndarray, doc_lang_offset: int = 0):
    """Macro Recall@5, MRR, nDCG@10 for one profile."""
    recalls, mrrs, ndcgs = [], [], []
    for qi, q in enumerate(queries):
        judgments = {j["doc_id"]: j["grade"] for j in q["judgments"]}
        relevant = {d for d, g in judgments.items() if g >= 1}
        order = np.argsort(-scores[qi], kind="stable")
        ranked_ids = [DOC_IDS[i] for i in order]
        top5 = set(ranked_ids[:TOP_K_RECALL])
        recalls.append(len(top5 & relevant) / min(TOP_K_RECALL, len(relevant)))
        first = next((r for r, did in enumerate(ranked_ids, 1) if did in relevant), None)
        mrrs.append(1.0 / first if first else 0.0)
        dcg = sum(
            judgments.get(did, 0) / np.log2(rank + 1)
            for rank, did in enumerate(ranked_ids[:NDCG_K], 1)
        )
        ideal = sorted(judgments.values(), reverse=True)[:NDCG_K]
        idcg = sum(g / np.log2(i + 1) for i, g in enumerate(ideal, 1))
        ndcgs.append(dcg / idcg if idcg else 0.0)
    return {
        "recall_at_5": float(np.mean(recalls)),
        "mrr": float(np.mean(mrrs)),
        "ndcg_at_10": float(np.mean(ndcgs)),
    }


DOC_IDS: list[str] = []


def cmd_quality() -> int:
    corpus, q4, fp32 = _load()
    n_semantic = 600
    n_docs = len(corpus["documents"])  # 600 semantic + 24 behavior
    docs = corpus["documents"][:n_semantic]
    queries = corpus["queries"]
    global DOC_IDS
    DOC_IDS = [d["doc_id"] for d in docs]

    # row order in the .npy files: all documents, then all queries
    q4_d, fp32_d = q4[:n_semantic], fp32[:n_semantic]
    q4_q, fp32_q = q4[n_docs:], fp32[n_docs:]

    paired = np.sum(q4 * fp32, axis=1)  # all 744 rows: docs then queries
    per_query_cos = paired[n_docs:]
    per_doc_cos = paired[:n_docs]

    results = {"paired_cosine": {}, "profiles": {}, "deltas": {}, "per_language": {}}
    for name, cos in (("documents", per_doc_cos), ("queries", per_query_cos)):
        results["paired_cosine"][name] = {
            "median": float(np.median(cos)),
            "p5": float(np.percentile(cos, 5)),
            "min": float(cos.min()),
        }
    results["paired_cosine"]["all"] = {
        "median": float(np.median(paired)),
        "p5": float(np.percentile(paired, 5)),
    }

    for profile, (dvec, qvec) in (("q4", (q4_d, q4_q)), ("fp32", (fp32_d, fp32_q))):
        scores = qvec @ dvec.T
        results["profiles"][profile] = _metrics_for(queries, scores)
        for lang in ("vi", "en"):
            idx = [i for i, q in enumerate(queries) if q["lang"] == lang]
            sub = [queries[i] for i in idx]
            results["per_language"].setdefault(profile, {})[lang] = _metrics_for(sub, scores[idx])

    for metric in ("recall_at_5", "mrr", "ndcg_at_10"):
        results["deltas"][metric] = (
            results["profiles"]["fp32"][metric] - results["profiles"]["q4"][metric]
        )
        results["deltas"][f"lang_max_{metric}"] = max(
            abs(results["per_language"]["fp32"][lang][metric]
                - results["per_language"]["q4"][lang][metric])
            for lang in ("vi", "en")
        )

    out = EVIDENCE / "quality_results.json"
    out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0


# --------------------------------------------------------------------------
def _rss_mib() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _vmrss_mib() -> float:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS"):
                return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return _rss_mib()


def cmd_resources() -> int:
    from q4_encode import load, embed_texts
    from payloads import query_payload

    corpus = json.loads((SPIKE / "corpus" / "embedding-quality-v1.json").read_text(encoding="utf-8"))
    queries = corpus["queries"]

    results = {"warm_latency_ms": {}, "cold_load_s": [], "memory": {}}
    raw_samples: dict[str, list[float]] = {}

    tokenizer, session = load()

    for bucket in (0, 1, 2):
        texts = [query_payload(q["text"]) for q in queries if q["bucket"] == bucket]
        rng = np.random.default_rng(20260804 + bucket)
        for i in range(50):  # warmup
            embed_texts(tokenizer, session, [texts[i % len(texts)]])
        steady_rss = _vmrss_mib()
        samples = []
        for i in range(500):
            start = time.perf_counter()
            embed_texts(tokenizer, session, [texts[rng.integers(len(texts))]])
            samples.append((time.perf_counter() - start) * 1000.0)
        raw_samples[f"bucket_{bucket}"] = samples
        arr = np.array(samples)
        results["warm_latency_ms"][f"bucket_{bucket}"] = {
            "p50": float(np.percentile(arr, 50)),
            "p95": float(np.percentile(arr, 95)),
            "p99": float(np.percentile(arr, 99)),
            "n": len(samples),
        }
        results["memory"][f"steady_rss_mib_bucket_{bucket}"] = steady_rss

    results["memory"]["peak_rss_mib"] = _rss_mib()

    # cold load: 10 fresh processes
    code = (
        "import sys, time; sys.path.insert(0, %r);"
        "from q4_encode import load;"
        "t=time.perf_counter(); load(); print(f'{time.perf_counter()-t:.4f}')"
        % str(SPIKE)
    )
    for _ in range(10):
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        )
        results["cold_load_s"].append(float(out.stdout.strip()))

    # two-process PSS (Linux)
    pss = []
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", code.replace("print", "import time as _t; _t.sleep(5); print")],
            stdout=subprocess.DEVNULL,
        )
        for _ in range(2)
    ]
    time.sleep(3)
    for p in procs:
        try:
            for line in Path(f"/proc/{p.pid}/smaps_rollup").read_text().splitlines():
                if line.startswith("Pss:"):
                    pss.append(int(line.split()[1]) / 1024.0)
        except OSError:
            pass
    for p in procs:
        p.wait()
    results["memory"]["two_process_pss_mib"] = pss

    (EVIDENCE / "latency_samples.json").write_text(json.dumps(raw_samples), encoding="utf-8")
    out = EVIDENCE / "resource_results.json"
    out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0


# --------------------------------------------------------------------------
def _git_state() -> dict:
    def run(*args):
        return subprocess.run(args, capture_output=True, text=True, check=True).stdout.strip()

    commit = run("git", "rev-parse", "HEAD")
    dirty_diff = subprocess.run(
        ["git", "diff", "--stat"], capture_output=True, text=True
    ).stdout
    dirty_hash = None
    if dirty_diff.strip():
        import hashlib
        dirty_hash = __import__("hashlib").sha256(dirty_diff.encode()).hexdigest()
    return {"commit": commit, "dirty_diff_sha256": dirty_hash}


def cmd_gate() -> int:
    quality = json.loads((EVIDENCE / "quality_results.json").read_text())
    resources = json.loads((EVIDENCE / "resource_results.json").read_text())
    manifest_corpus = json.loads((SPIKE / "corpus" / "manifest.json").read_text())

    warm_p95_max = max(
        resources["warm_latency_ms"][b]["p95"] for b in ("bucket_0", "bucket_1", "bucket_2")
    )
    measured = {
        "paired_cosine_median": quality["paired_cosine"]["all"]["median"],
        "paired_cosine_p5": quality["paired_cosine"]["all"]["p5"],
        "q4_recall_at_5": quality["profiles"]["q4"]["recall_at_5"],
        "q4_mrr": quality["profiles"]["q4"]["mrr"],
        "q4_ndcg_at_10": quality["profiles"]["q4"]["ndcg_at_10"],
        "delta_recall_at_5": quality["deltas"]["recall_at_5"],
        "delta_mrr": quality["deltas"]["mrr"],
        "delta_ndcg_at_10": quality["deltas"]["ndcg_at_10"],
        "delta_lang_recall_at_5": quality["deltas"]["lang_max_recall_at_5"],
        "delta_lang_mrr": quality["deltas"]["lang_max_mrr"],
        "delta_lang_ndcg_at_10": quality["deltas"]["lang_max_ndcg_at_10"],
        "warm_p95_ms_le_128_tokens": warm_p95_max,
        "steady_rss_mib": max(
            v for k, v in resources["memory"].items() if k.startswith("steady_rss")
        ),
    }

    verdicts = {}
    for name, rule in THRESHOLDS.items():
        value = measured[name]
        ok = value >= rule["value"] if rule["op"] == ">=" else value <= rule["value"]
        verdicts[name] = {"value": value, "threshold": f"{rule['op']} {rule['value']}",
                          "pass": bool(ok)}

    overall = all(v["pass"] for v in verdicts.values())
    import onnxruntime
    import tokenizers

    run_id = datetime.now(timezone.utc).strftime("q4gate-%Y%m%dT%H%M%SZ")
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "utc": datetime.now(timezone.utc).isoformat(),
        "git": _git_state(),
        "command": "uv run python spikes/fp32/run_evaluation.py {quality,resources,gate}",
        "environment": {
            "os": platform.platform(), "kernel": platform.release(),
            "architecture": platform.machine(), "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "versions": {
            "onnxruntime": onnxruntime.__version__,
            "tokenizers": tokenizers.__version__,
            "numpy": np.__version__,
        },
        "reference_machine_sha256": (ROOT / "benchmarks" / "reference-machine.json.sha256")
        .read_text().split()[0],
        "corpus": {
            "corpus_id": manifest_corpus["corpus_id"],
            "corpus_sha256": manifest_corpus["corpus_sha256"],
            "seed": manifest_corpus["seed"],
            "row_counts": manifest_corpus["row_counts"],
        },
        "models": manifest_corpus["models"],
        "payload_input_version": 2,
        "protocol": {
            "embedding": "batch 1, 50 warmups, 500 measured per token bucket",
            "cold_load": "10 fresh processes",
            "quality": "macro Recall@5 (grade>=1), MRR, nDCG@10 over 600 semantic docs",
        },
        "quality": quality,
        "resources": resources,
        "thresholds": verdicts,
        "overall_pass": overall,
    }
    out = EVIDENCE / f"manifest-{run_id}.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    for name, v in verdicts.items():
        mark = "PASS" if v["pass"] else "FAIL"
        print(f"  {mark}  {name}: {v['value']:.6g} ({v['threshold']})")
    print(f"gate: {'PASS' if overall else 'FAIL'} -> {out}")
    return 0 if overall else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["quality", "resources", "gate"])
    args = parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    if args.mode == "quality":
        return cmd_quality()
    if args.mode == "resources":
        return cmd_resources()
    return cmd_gate()


if __name__ == "__main__":
    sys.exit(main())
