# Changelog

## 0.11.0 — unreleased

First release of the standalone embedded runtime. Another Brain is now a
single installed executable: `uv tool install another-brain`, then
`another-brain connect <harness>`. There is no server, container, daemon, or
external database in the install, runtime, or deployment model.

### Highlights

- **Zero-server MCP tool.** The bare `another-brain` command *is* the MCP
  stdio server; harnesses register it as `{"command": "another-brain"}`.
  Loopback HTTP (`serve --http`, numeric loopback only) stays available but
  is opt-in and is not used by any connector.
- **One SQLite file.** Ordinary tables + FTS5, one `brain.sqlite3` in the
  per-user data directory. Hybrid retrieval fuses weighted BM25 (5:3:1) and
  exact cosine vectors with RRF (k=60) — no ANN index, no approximation.
- **Local embeddings.** Harrier OSS v1 270M q4 via raw ONNX Runtime on CPU,
  pinned and hash-verified at install; no network after `model pull`, no
  Torch, no SentenceTransformers.
- **Self-expiring diary.** Importance 5..1 → 365/180/90/30/7 days; soft
  delete with a 30-day grace window; a structural audit trail that never
  stores memory text.
- **Eight stable tools:** `brain_remember`, `brain_search`, `brain_recent`,
  `brain_get`, `brain_reinforce`, `brain_forget`, plus the audit and admin
  surfaces.
- **`another-brain connect`** — one cross-platform command that registers
  the MCP server and installs the bundled skill for `claude-code`, `codex`,
  `cursor`, `gemini-cli`, and `pi`. No manual JSON, no repo clone, no Node.
- **`another-brain doctor`** — platform support tier, resolved paths,
  package version, per-file model hashes, a read-only check of the real
  database, and an isolated write/read/delete probe in a throwaway database.
  It never loads the model, never downloads, never writes to the real store.
- **JSONL v1 import** (`import-jsonl`) with resumable checkpoints.

### Removed

- **Redis, Docker, and Torch are gone** — not optional, not behind a flag.
  The 0.11.0 runtime has no storage-backend switch; the Redis-era prototype
  remains only as git history (`main@edc0e57`) and as a behavior oracle for
  the test suite.
- **The scope partition was removed from the memory contract.** Memories are
  partitioned by brain only; `scope`/`scope_id` no longer exist in the
  schema, the tool payloads, or the JSONL v1 envelope.
- **The shell harness connectors were retired.** `another-brain connect`
  replaces them on every OS.

### Platform support

Every platform gap is dependency-wheel availability, not product code: the
package itself ships a pure `py3-none-any` wheel. `another-brain doctor`
reports the tier for the machine it runs on.

| Platform | Tier | Vector backend |
|---|---|---|
| Linux x86_64 (glibc) | **Supported** — CI-gated | sqlite-vec |
| macOS 14+ Apple Silicon | **Supported** — CI-gated | sqlite-vec |
| Windows 10/11 x86_64 | **Supported** — CI-gated | sqlite-vec |
| Linux aarch64 (glibc) | Best-effort — resolves, no CI hardware | sqlite-vec |
| Windows ARM64 | Best-effort — resolves, no CI hardware | NumPy fallback |
| macOS Intel | Unsupported — onnxruntime ≥1.28 ships no wheel | — |
| macOS 13 and older | Unsupported — onnxruntime 1.28 requires macOS 14+ | — |
| Alpine / musl | **Uninstallable** — resolution fails, no musl wheels | — |
| 32-bit Windows, ARMv7, ppc64le, s390x | Unsupported | — |

Python 3.12–3.14. Unsupported platforms fail fast at `uv tool install` or
are named explicitly by `doctor`; nothing degrades into a silent source
build. On Windows ARM64 `sqlite-vec` is excluded by a dependency marker
(it ships no `win_arm64` wheel and no sdist), so the install succeeds and
retrieval runs on the NumPy exact fallback.

The supported row is gated by the `wheel-gate` CI matrix — 4 OS images ×
Python 3.12/3.13/3.14, wheel install + typed-CLI contract + unit suite +
one forced-NumPy-fallback pass per OS (run `31089526222`: 8/8 wheel cells,
4/4 unit cells green).

### Resource envelope

Measured on the reference machine recorded in `benchmark.md` (AMD Ryzen AI 9
HX 370, 31 GiB RAM, CPython 3.14.6, onnxruntime defaults).

- **~322 MiB RSS / ~321 MiB PSS per process** once the embedding session is
  loaded — the budget to plan for when several harnesses each run their own
  `another-brain` process. Two concurrent processes measured ≈318 MiB PSS
  each; there is no shared embedding daemon by design.
- Constructing the provider loads nothing (52.4 MiB interpreter baseline);
  the model is loaded lazily on first embed. `close()` returns only ~20 MiB
  (onnxruntime arena) — real reclamation happens at process exit.
- Cold load 0.78–0.86 s over 10 fresh processes. Warm encode p95 68.7 ms at
  ≤128 tokens.
- Store size scales linearly with the 640-dim FLOAT32 embedding: ≈4 MB per
  1k memories (38.6 MB at 10k, 382.7 MB at 100k).

### Retrieval quality

Permanent product gate against the pinned q4 profile, 600 docs / 120 judged
queries (60 VI / 60 EN): **Recall@5 0.9317, MRR 0.9431, nDCG@10 0.8380**.
Against the Redis-era legacy oracle on the same judged corpus the embedded
stack wins on every aggregate (+0.078 Recall@5, +0.075 MRR, +0.084 nDCG@10).

q4 quantization costs ~2% in absolute paired cosine against the fp32 oracle
(median 0.9808) but matches or beats it on ranking — every retrieval delta
passes with wide margin.

### Retrieval latency, including the fallback

Judged suite, fused top-10, pooled p95 across 5 × 1,000 measured queries per
cell:

| Store | vector p95 — sqlite-vec | vector p95 — NumPy fallback | hybrid p95 — sqlite-vec | hybrid p95 — NumPy |
|---|---|---|---|---|
| 1k | 1.91 ms | 3.08 ms | 4.73 ms | 5.90 ms |
| 10k | 3.32 ms | 5.62 ms | 11.65 ms | 14.16 ms |
| 50k | 13.79 ms | 20.89 ms | 59.11 ms | 60.29 ms |
| 100k | 25.49 ms | 35.84 ms | 116.92 ms | 108.86 ms |

**The NumPy fallback is stated openly:** it is a compatibility path, not a
performance path. It costs roughly 1.4× the vector-branch latency at every
measured size, and it scans row by row on purpose — stacking the candidate
BLOBs into one matmul is ~1.26× faster but would allocate up to 917 MB when
every live row is a candidate, over the memory budget. Bounded memory
outranks the milliseconds on exactly the platforms that need the fallback.
Both backends return byte-identical rankings (parity contract: raw |Δ| ≤
1e-6, max observed 9.48e-07).

**The lexical (BM25) branch has no locked latency budget, by decision.** It
is the slower of the two branches at scale — 8.78 ms p95 at 10k, recorded at
71.68 ms p95 on the 100k store — and its cost is driven by MATCH-term
selectivity rather than store size: a 63-term query matches 58.6% of a 10k
store, and the term `"the"` alone matches 49.8%. Document-frequency
filtering would buy ~18% at the safe end and is deliberately not
implemented: it changes the retrieval contract and makes behavior depend on
store size. Documented, not budgeted.

> **Measurement caveat.** These latency figures were measured before the
> scope partition was removed from the memory contract, which also dropped
> two leading columns from the three composite `memories` indexes. The
> narrower index is expected to be neutral or faster, and the quality
> metrics are unaffected (the judged corpus occupies a single scope, so the
> removed predicates matched every row), but the latency numbers have not
> been re-measured on the shipped schema.

Full evidence, including the failed runs, the revised q4 thresholds and why,
the concurrency validation, and the thermal incident during the first
100k attempt, is in `benchmark.md`.
