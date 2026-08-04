"""Deterministic benchmark store generator (TASK-005).

Builds SQLite stores of 1k/10k/50k/100k rows with realistic
text/scope/importance/expiry/deletion distributions and recorded seeds. Each
store embeds the 624 embedding-quality-v1 corpus documents (fake vectors for
now — TASK-063 regenerates with real q4 embeddings before the judged suite)
plus seeded fillers.

Run from the repo root:  uv run python benchmarks/generate_stores.py [sizes...]
Output: benchmarks/stores/brain-bench-<size>.sqlite3 (gitignored, reproducible)
"""
from __future__ import annotations

import json
import random
import sqlite3
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SPIKE = ROOT / "spikes" / "fp32"
STORES = ROOT / "benchmarks" / "stores"
sys.path.insert(0, str(SPIKE))

from build_corpus import (  # noqa: E402
    ASPECTS, CLUSTERS, EN_CONTENT_T, EN_SUMMARY_T, VI_CONTENT_T, VI_SUMMARY_T,
    fill,
)

SEED = 20260804
NOW_MS = 1_785_000_000_000
BRAIN_ID = "bench-brain"
PAGE_SIZE = 16384

SCHEMA = """
CREATE TABLE memories(
  row_id INTEGER PRIMARY KEY,
  memory_id TEXT NOT NULL,
  brain_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  topic TEXT NOT NULL,
  catalog TEXT NOT NULL,
  summary TEXT NOT NULL,
  content TEXT NOT NULL,
  timeline_day TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL,
  importance INTEGER NOT NULL,
  expires_at_ms INTEGER NOT NULL,
  deleted_at_ms INTEGER,
  metadata_json TEXT NOT NULL,
  embedding BLOB NOT NULL,
  record_version INTEGER NOT NULL
);
CREATE VIRTUAL TABLE memory_fts USING fts5(
  topic, summary, content,
  content='memories', content_rowid='row_id',
  tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memory_fts(rowid, topic, summary, content)
  VALUES (new.row_id, new.topic, new.summary, new.content);
END;
CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, topic, summary, content)
  VALUES ('delete', old.row_id, old.topic, old.summary, old.content);
END;
CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, topic, summary, content)
  VALUES ('delete', old.row_id, old.topic, old.summary, old.content);
  INSERT INTO memory_fts(rowid, topic, summary, content)
  VALUES (new.row_id, new.topic, new.summary, new.content);
END;
"""

TTL_DAYS = {5: 365, 4: 180, 3: 90, 2: 30, 1: 7}
IMPORTANCE_WEIGHTS = [(1, 15), (2, 20), (3, 30), (4, 20), (5, 15)]


def fake_vector(rng: np.random.Generator) -> bytes:
    vec = rng.standard_normal(640, dtype=np.float32)
    vec /= np.linalg.norm(vec)
    return vec.astype("<f4").tobytes()


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


def build_store(size: int) -> Path:
    rng = random.Random(SEED + size)
    vec_rng = np.random.default_rng(SEED + size)
    corpus = json.loads((SPIKE / "corpus" / "embedding-quality-v1.json").read_text(encoding="utf-8"))
    corpus_docs = corpus["documents"]

    path = STORES / f"brain-bench-{size}.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    con = sqlite3.connect(path)
    con.execute(f"PRAGMA page_size={PAGE_SIZE}")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)

    rows = []

    def lifecycle(rng: random.Random, created: int, importance: int):
        expires = created + TTL_DAYS[importance] * 86_400_000
        deleted = None
        roll = rng.random()
        if roll < 0.05:
            expires = NOW_MS - rng.randint(1, 1000) * 86_400_000  # expired
        elif roll < 0.10:
            deleted = created + rng.randint(0, 10) * 86_400_000   # soft-deleted
        return expires, deleted

    created = NOW_MS - 365 * 86_400_000
    for i, d in enumerate(corpus_docs[: min(size, len(corpus_docs))]):
        created += 86_400_000 // 6
        expires, deleted = lifecycle(rng, created, d["importance"])
        rows.append((
            d["doc_id"], BRAIN_ID, "bench-agent", "project", "proj-1",
            d["topic"], d["catalog"], d["summary"], d["content"], "2026-07-15",
            created, created, d["importance"], expires, deleted, "{}",
            fake_vector(vec_rng), 1,
        ))
    for i in range(size - len(rows)):
        created += 86_400_000 // 6
        cluster_id, topic, summary, content = filler_text(rng)
        scope, scope_id = pick_scope(rng)
        importance = pick_importance(rng)
        expires, deleted = lifecycle(rng, created, importance)
        rows.append((
            f"bench-{size}-{i:07d}", BRAIN_ID, "bench-agent", scope, scope_id,
            topic, "note", summary, content, "2026-07-15",
            created, created, importance, expires, deleted, "{}",
            fake_vector(vec_rng), 1,
        ))

    with con:
        con.executemany(
            "INSERT INTO memories(memory_id, brain_id, agent_id, scope, scope_id,"
            " topic, catalog, summary, content, timeline_day, created_at_ms,"
            " updated_at_ms, importance, expires_at_ms, deleted_at_ms,"
            " metadata_json, embedding, record_version)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        con.execute("INSERT INTO memory_fts(memory_fts) VALUES('optimize')")
    con.close()
    # fresh connection: checkpoint WAL back into the main file
    con = sqlite3.connect(path)
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    return path


def main() -> int:
    sizes = [int(a) for a in sys.argv[1:]] or [1_000, 10_000, 50_000, 100_000]
    for size in sizes:
        path = build_store(size)
        mb = path.stat().st_size / 1e6
        print(f"store {size:>7}: {path.name} ({mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
