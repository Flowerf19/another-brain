"""Deterministic judged-suite store builder (TASK-063).

Regenerates the 1k/10k/50k/100k benchmark stores on the REAL v1 schema
(replacing the TASK-005 diagnostic stores, whose interim schema predates the
locked DDL). The 624 embedding-quality-v1 corpus documents carry REAL q4
embeddings from the production provider (cached in ``corpus-q4-vectors.npy``,
keyed by corpus + manifest hashes); fillers are seeded random unit vectors —
exact-scan cost is data-independent and judged quality involves corpus docs
only, so filler vectors affect neither metric honesty. Text/scope/importance/
expiry/deletion distributions mirror TASK-005 with recorded seeds.

Corpus documents live in one scope (``project/proj-1``) so all judged
relevant docs are visible to the judged queries; fillers follow the scope
mix, so some land in ``proj-1`` as realistic distractors.

Run from the repo root:

    uv run python benchmarks/retrieval/build_stores.py [sizes...]

Output: ``benchmarks/stores/brain-bench-<size>.sqlite3`` (gitignored,
reproducible from the recorded seed).
"""
from __future__ import annotations

import json
import random
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SPIKE = ROOT / "spikes" / "fp32"
STORES = ROOT / "benchmarks" / "stores"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SPIKE))

from build_corpus import (  # noqa: E402
    ASPECTS, CLUSTERS, EN_CONTENT_T, EN_SUMMARY_T, VI_CONTENT_T, VI_SUMMARY_T,
    fill,
)

from another_brain.services.embedding.model_installer import (  # noqa: E402
    is_installed, profile_dir,
)
from another_brain.services.embedding.model_manifest import (  # noqa: E402
    MODEL_MANIFEST, manifest_digest,
)
from another_brain.services.sql.connection import SQLiteConnectionFactory  # noqa: E402
from another_brain.services.sql.migrations import migrate  # noqa: E402

SEED = 20260804
NOW_MS = 1_785_000_000_000
BRAIN_ID = "bench-brain"
CORPUS_SCOPE = ("project", "proj-1")
SIZES = (1000, 10000, 50000, 100000)

TTL_DAYS = {5: 365, 4: 180, 3: 90, 2: 30, 1: 7}
IMPORTANCE_WEIGHTS = [(1, 15), (2, 20), (3, 30), (4, 20), (5, 15)]

VECTORS_CACHE = HERE / "corpus-q4-vectors.npy"
VECTORS_META = HERE / "corpus-q4-vectors.meta.json"


def corpus_vectors(corpus: dict) -> np.ndarray:
    """Real q4 embeddings for the 624 corpus docs (hash-keyed cache)."""
    corpus_sha = hashlib_sha256(CORPUS_PATH.read_bytes())
    digest = manifest_digest()
    if VECTORS_CACHE.exists() and VECTORS_META.exists():
        meta = json.loads(VECTORS_META.read_text())
        if meta.get("corpus_sha256") == corpus_sha and meta.get("manifest_digest") == digest:
            return np.load(VECTORS_CACHE)
    from another_brain.config import AppConfig
    from another_brain.services.embedding.provider import ONNXEmbeddingProvider

    config = AppConfig.from_env()
    if not is_installed(config.model_cache_dir, verify_files=False):
        raise SystemExit("the pinned q4 profile is not installed; run `another-brain model pull`")
    provider = ONNXEmbeddingProvider(profile_dir(config.model_cache_dir))
    vectors = np.stack(
        [
            provider.embed_document(topic=d["topic"], summary=d["summary"]).values
            for d in corpus["documents"]
        ]
    )
    np.save(VECTORS_CACHE, vectors)
    VECTORS_META.write_text(json.dumps({
        "corpus_sha256": corpus_sha,
        "manifest_digest": digest,
        "documents": len(vectors),
    }, indent=1))
    return vectors


def hashlib_sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


CORPUS_PATH = SPIKE / "corpus" / "embedding-quality-v1.json"


def pick_importance(rng: random.Random) -> int:
    total = sum(w for _, w in IMPORTANCE_WEIGHTS)
    roll = rng.uniform(0, total)
    for value, weight in IMPORTANCE_WEIGHTS:
        roll -= weight
        if roll <= 0:
            return value
    return 3


def pick_scope(rng: random.Random) -> tuple[str, str]:
    roll = rng.random()
    if roll < 0.5:
        return "user", f"user-{rng.randint(1, 20)}"
    if roll < 0.9:
        return "project", f"proj-{rng.randint(1, 10)}"
    return "global", "global"


def filler_text(rng: random.Random) -> tuple[str, str, str, str]:
    cluster = rng.choice(CLUSTERS)
    subject = rng.choice(cluster["subjects"])
    clause, _ = rng.choice(ASPECTS[cluster["lang"]])
    summ_t = EN_SUMMARY_T if cluster["lang"] == "en" else VI_SUMMARY_T
    cont_t = EN_CONTENT_T if cluster["lang"] == "en" else VI_CONTENT_T
    summary = f"{fill(rng.choice(summ_t), subject['vocab'], rng, rng.randint(2, 14))} — {clause}."
    content = fill(rng.choice(cont_t), subject["vocab"], rng, rng.randint(2, 14))
    return cluster["id"], subject["topic"], summary, content


def lifecycle(rng: random.Random, created: int, importance: int):
    expires = created + TTL_DAYS[importance] * 86_400_000
    deleted = None
    roll = rng.random()
    if roll < 0.05:
        expires = NOW_MS - rng.randint(1, 1000) * 86_400_000  # expired
    elif roll < 0.10:
        deleted = created + rng.randint(0, 10) * 86_400_000   # soft-deleted
    return expires, deleted


def day_of(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def insert_rows(con: sqlite3.Connection, rows: list[tuple]) -> None:
    sql = (
        "INSERT INTO memories(memory_id, brain_id, agent_id, scope, scope_id,"
        " topic, catalog, summary, content, timeline_day, period_start_ms,"
        " period_end_ms, created_at_ms, updated_at_ms, importance,"
        " expires_at_ms, deleted_at_ms, metadata, profile_id, embedding,"
        " record_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )
    for start in range(0, len(rows), 2000):
        con.execute("BEGIN IMMEDIATE")
        con.executemany(sql, rows[start : start + 2000])
        con.commit()


def build_store(size: int, corpus: dict, vectors: np.ndarray) -> Path:
    rng = random.Random(SEED + size)
    vec_rng = np.random.default_rng(SEED + size)
    corpus_docs = corpus["documents"]

    path = STORES / f"brain-bench-{size}.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        target = Path(str(path) + suffix)
        if target.exists():
            target.unlink()

    factory = SQLiteConnectionFactory(path)
    factory.bootstrap()
    migrate(factory.db_path)

    rows: list[tuple] = []
    created = NOW_MS - 365 * 86_400_000

    for i, d in enumerate(corpus_docs[: min(size, len(corpus_docs))]):
        created += 86_400_000 // 6
        expires = d["expires_at_ms"]
        deleted = d["deleted_at_ms"]
        rows.append((
            d["doc_id"], BRAIN_ID, "bench-agent", CORPUS_SCOPE[0], CORPUS_SCOPE[1],
            d["topic"], d["catalog"], d["summary"], d["content"],
            day_of(d["created_at_ms"]), None, None, d["created_at_ms"],
            d["created_at_ms"], d["importance"], expires, deleted, "{}",
            MODEL_MANIFEST.profile, vectors[i].astype("<f4").tobytes(), 1,
        ))
    for _ in range(size - len(rows)):
        created += 86_400_000 // 6
        catalog, topic, summary, content = filler_text(rng)
        scope, scope_id = pick_scope(rng)
        importance = pick_importance(rng)
        expires, deleted = lifecycle(rng, created, importance)
        vec = vec_rng.standard_normal(640, dtype=np.float32)
        vec /= np.linalg.norm(vec)
        rows.append((
            f"fill-{created}-{len(rows)}", BRAIN_ID, "bench-agent", scope, scope_id,
            topic, catalog, summary, content, day_of(created), None, None,
            created, created, importance, expires, deleted, "{}",
            MODEL_MANIFEST.profile, vec.astype("<f4").tobytes(), 1,
        ))

    con = sqlite3.connect(path)
    con.execute("PRAGMA busy_timeout=5000")
    con.execute(
        "INSERT INTO embedding_profiles(profile_id, model_repo, model_revision,"
        " variant, dimension, dtype, normalized, tokenizer_sha256, config_sha256,"
        " prompt_utf8_sha256, query_prompt, input_version, created_at_ms)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            MODEL_MANIFEST.profile, MODEL_MANIFEST.repo, MODEL_MANIFEST.revision,
            MODEL_MANIFEST.profile, MODEL_MANIFEST.dimensions, MODEL_MANIFEST.dtype,
            1, dict(MODEL_MANIFEST.files)["tokenizer.json"],
            dict(MODEL_MANIFEST.files)["config.json"],
            MODEL_MANIFEST.query_prompt_utf8_sha256, MODEL_MANIFEST.query_prompt,
            MODEL_MANIFEST.input_version, NOW_MS,
        ),
    )
    con.commit()
    insert_rows(con, rows)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    return path


def main() -> int:
    sizes = [int(arg) for arg in sys.argv[1:]] or list(SIZES)
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    vectors = corpus_vectors(corpus)
    for size in sizes:
        path = build_store(size, corpus, vectors)
        print(f"built {path} ({path.stat().st_size / 1e6:.1f} MB, {size} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
