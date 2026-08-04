"""Self-validation for the embedding-quality-v1 corpus + manifest (TASK-002).

A missing field or hash mismatch invalidates the run (exit 1). Run from the
repo root:  uv run python spikes/fp32/validate_corpus.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from tokenizers import Tokenizer

SPIKE = Path(__file__).resolve().parent
CORPUS = SPIKE / "corpus" / "embedding-quality-v1.json"
MANIFEST = SPIKE / "corpus" / "manifest.json"

EXPECTED_PROMPT_HASH = "df4b2898bf22e00bacddddd489243a3f8793730e38b842ec10161cebd94d36d6"
MANIFEST_REQUIRED = {
    "schema_version", "corpus_id", "corpus_sha256", "source", "license",
    "seed", "generator_commit", "row_counts", "token_buckets", "judgments",
    "models", "tokenizer_config_prompt_hashes", "payload_input_version",
}

failures = []


def check(cond: bool, message: str) -> None:
    if not cond:
        failures.append(message)


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    corpus_bytes = CORPUS.read_bytes()
    corpus = json.loads(corpus_bytes)

    missing = MANIFEST_REQUIRED - set(manifest)
    check(not missing, f"manifest missing fields: {sorted(missing)}")
    check(
        hashlib.sha256(corpus_bytes).hexdigest() == manifest["corpus_sha256"],
        "corpus sha256 mismatch with manifest",
    )
    check(manifest["corpus_id"] == "embedding-quality-v1", "wrong corpus_id")
    check(manifest["payload_input_version"] == 2, "payload input version must be 2")
    check(
        manifest["tokenizer_config_prompt_hashes"]["query_prompt_utf8_sha256"]
        == EXPECTED_PROMPT_HASH,
        "query prompt hash mismatch",
    )

    docs = {d["doc_id"]: d for d in corpus["documents"]}
    queries = corpus["queries"]
    behavior = corpus["behavior_cases"]

    counts = manifest["row_counts"]
    check(len(docs) == counts["documents"], "document count mismatch")
    check(len(queries) == 120, "query count must be 120")
    vi = [q for q in queries if q["lang"] == "vi"]
    en = [q for q in queries if q["lang"] == "en"]
    check(len(vi) == 60 and len(en) == 60, "language partitions must be 60/60")
    check(sum(1 for q in vi if q["no_diacritic"]) == 20, "need 20 no-diacritic VI queries")
    check(len(behavior) == 24, "behavior partition must have 24 cases")
    kinds = {}
    for case in behavior:
        kinds[case["kind"]] = kinds.get(case["kind"], 0) + 1
    check(
        kinds == {"content_only_identifier": 12, "punctuation_only_query": 6,
                  "expired_deleted_starvation": 6},
        f"behavior kind split wrong: {kinds}",
    )

    tokenizer = Tokenizer.from_file(str(SPIKE / ".models" / "q4" / "tokenizer.json"))
    buckets = [0, 0, 0]
    for q in queries:
        tokens = len(tokenizer.encode(q["text"], add_special_tokens=False).ids)
        check(tokens == q["raw_tokens"], f"{q['query_id']}: recorded token count stale")
        bucket = 0 if tokens <= 16 else (1 if tokens <= 64 else 2)
        check(bucket == q["bucket"], f"{q['query_id']}: bucket mismatch")
        check(not (bucket == 2 and tokens > 107), f"{q['query_id']}: prompted total would exceed 128")
        buckets[bucket] += 1
        grades = sorted(j["grade"] for j in q["judgments"])
        check(grades == [0, 0, 0, 0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 3],
              f"{q['query_id']}: judgment grades {grades}")
        check(len({j["doc_id"] for j in q["judgments"]}) == 14, f"{q['query_id']}: duplicate judged docs")
        for j in q["judgments"]:
            check(j["doc_id"] in docs, f"{q['query_id']}: unknown judged doc {j['doc_id']}")
        check(
            any(j["grade"] == 3 and docs[j["doc_id"]]["lang"] == q["lang"] for j in q["judgments"]),
            f"{q['query_id']}: grade-3 doc must match query language",
        )
    check(buckets == [40, 40, 40], f"bucket distribution {buckets}")

    for case in behavior:
        if case["kind"] in ("content_only_identifier", "expired_deleted_starvation"):
            check(case["expect_doc_id"] in docs, f"{case['case_id']}: expect_doc missing")
        if case["kind"] == "expired_deleted_starvation":
            stale = docs[case["stale_doc_id"]]
            is_stale = stale["deleted_at_ms"] is not None or stale["expires_at_ms"] <= corpus["now_ms"]
            check(is_stale, f"{case['case_id']}: stale doc is not stale at now_ms")
            live = docs[case["expect_doc_id"]]
            check(
                live["deleted_at_ms"] is None and live["expires_at_ms"] > corpus["now_ms"],
                f"{case['case_id']}: live tail is not live",
            )

    # semantic docs are live at now_ms
    for d in docs.values():
        if d["partition"] == "semantic":
            check(
                d["deleted_at_ms"] is None and d["expires_at_ms"] > corpus["now_ms"],
                f"{d['doc_id']}: semantic doc not live",
            )

    if failures:
        for f in failures[:20]:
            print(f"INVALID: {f}", file=sys.stderr)
        print(f"FAIL: {len(failures)} violation(s)", file=sys.stderr)
        return 1
    print("PASS: embedding-quality-v1 corpus + manifest valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
