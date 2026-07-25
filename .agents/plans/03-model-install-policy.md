---
status: approved
approved: 2026-07-10
owner: architecture
created: 2026-07-09
last_updated: 2026-07-09
scope: step-03
depends_on: .agents/plans/02-directory-and-class-architecture.md
---

# Step 03 - Model Install And Download Policy

This step defines how Another Brain should handle local model downloads during
installation and first run. It is policy-only: do not implement download code,
package hooks, Docker build steps, or provider integrations until this review is
accepted.

## Recommendation

Do not download large models implicitly during package installation.

Use explicit or lazy download instead:

1. If the deployment uses an external provider (`openai_compat`, `gemini`, or a
   remote embedding API), no local model is downloaded.
2. If the deployment uses a local provider, the server checks the model cache at
   startup.
3. Missing local models are handled by a configured policy:
   - `disabled`: fail with a clear setup error;
   - `lazy`: download on first use;
   - `on_start`: download during service startup;
   - `manual`: require an explicit command before startup is considered ready.
4. Any future package installer must never download large model files. It may
   call or document the server-side model install command after user opt-in.
   (The npm launcher referenced here was cut 2026-07-25; the principle stands
   for any packaging added later.)

Default policy: `manual` for production-like installs, `lazy` for local
developer quickstart only after explicit opt-in.

## Why Not Install-Time Auto Download

Install-time model download is fragile:

- package installs become slow and failure-prone;
- CI and offline installs become unpredictable;
- package install hooks are a poor place to fetch hundreds of MB or GB;
- Docker builds become large and less cache-friendly;
- users may not have accepted model licenses or chosen a provider yet;
- model revisions and embedding dimensions affect Redis index compatibility.

Another Brain should keep installation separate from model acquisition.

## Provider Modes

`EmbeddingProviderMode`

- `external`: model is managed outside Another Brain.
- `local_cached`: model must exist in the configured cache.
- `local_downloadable`: Another Brain may download the model when policy allows.

`MemoryModelProviderMode`

- Same shape as embedding provider mode, but used for the lightweight memory
  model.
- Memory model changes do not require Redis vector reindex unless canonical
  stored content is regenerated.

## Proposed Config

```text
MODEL_DOWNLOAD_POLICY=manual | disabled | lazy | on_start
MODEL_CACHE_DIR=./.cache/another-brain/models
MODEL_ALLOW_NETWORK=false
MODEL_REGISTRY_URL=
MODEL_PINNED_REVISION=
MODEL_VERIFY_CHECKSUM=true
MODEL_MAX_DOWNLOAD_BYTES=
```

Runtime precision should be explicit:

```text
MODEL_WEIGHT_PRECISION=auto | fp32 | fp16 | bf16 | int8 | q8 | q4
EMBEDDING_OUTPUT_PRECISION=float32 | int8 | uint8
REDIS_VECTOR_DTYPE=FLOAT32
```

Default:

```text
MODEL_WEIGHT_PRECISION=auto
EMBEDDING_OUTPUT_PRECISION=float32
REDIS_VECTOR_DTYPE=FLOAT32
```

Embedding-specific config stays separate:

```text
EMBEDDING_PROVIDER=openai_compat | ollama | gemini | local
EMBEDDING_MODEL=...
EMBEDDING_DIM=...
```

Memory-model config also stays separate:

```text
MEMORY_MODEL_PROVIDER=openai_compat | ollama | gemini | local
MEMORY_MODEL_NAME=...
```

## Embedding Precision And Quantization

Separate these two decisions:

1. **Model weight precision** controls how the local model is loaded and run.
   This is where fp32, fp16, bf16, int8/Q8, and q4 belong.
2. **Embedding output precision** controls the vectors produced by the model and
   written to Redis.

For MVP, store Redis vectors as `FLOAT32`.

Reasons:

- RediSearch vector examples and default client vectorizer behavior are centered
  on `FLOAT32`.
- `FLOAT32` avoids calibration complexity for early correctness testing.
- embedding dimension, model name, and vector dtype become part of the index
  contract; changing dtype later should be treated as a migration/reindex.
- Harrier's 640-dim vectors cost about 2.5 KB per memory before index overhead,
  which is acceptable for MVP correctness.

Lower-precision embedding outputs are allowed later, but only as an explicit
storage mode:

- `int8` or `uint8` embedding vectors need calibration/range policy and recall
  evaluation before becoming default.
- binary embedding modes are out of scope for MVP Redis storage.
- any non-FLOAT32 Redis vector dtype must be recorded in health output and index
  metadata.

Model weight precision can be lower than Redis vector precision:

- A local embedding model may run with fp16/bf16/int8/q4 weights to save memory.
- The produced embedding should still be converted to normalized `float32`
  before Redis storage in MVP.
- Q8/Q4 should be described as runtime weight quantization, not as the Redis
  vector format.

Recommended starting profile for local Harrier:

```text
MODEL_WEIGHT_PRECISION=auto
EMBEDDING_OUTPUT_PRECISION=float32
REDIS_VECTOR_DTYPE=FLOAT32
NORMALIZE_EMBEDDINGS=true
```

Use `auto` to select fp16/bf16 on supported accelerators and fp32 on CPU unless
the implementation proves a safe lower-precision path. Add Q8/Q4 only after a
benchmark compares recall quality, latency, memory use, and compatibility with
the selected inference stack.

## Harrier Default Profile

For `microsoft/harrier-oss-v1-270m`, use the SentenceTransformers package path
as the default implementation path.

Verified model facts to preserve in implementation:

- model card reports 270M parameters, 640 embedding dimensions, 32,768 max
  tokens, and BF16 tensor type;
- Transformers config uses `Gemma3TextModel` with hidden size 640;
- SentenceTransformers module chain is Transformer -> Pooling -> Normalize;
- pooling is last-token pooling;
- query prompts matter, so the provider should support configured query prompt
  names/instructions instead of embedding every query as an unprompted passage.

Default Redis vector contract for Harrier:

```text
REDIS_VECTOR_DTYPE=FLOAT32
EMBEDDING_DIM=640
DISTANCE_METRIC=COSINE
NORMALIZE_EMBEDDINGS=true
```

For Redis HASH storage, serialize as packed float32 bytes equivalent to:

```text
np.asarray(embedding, dtype=np.float32).tobytes()
```

Implementation should still verify the returned vector length and dtype before
storing. At 640 dimensions, raw vector payload is 2,560 bytes per memory before
Redis/index overhead.

Use `FLAT` index mode for early exactness or small corpora; use `HNSW` when
latency and scale matter. The index mode is a storage contract decision and
should be captured in Step 04.

Do not default Harrier to Q4. Try Q8 only if runtime memory or latency requires
it, and only after comparing recall/ranking drift against the default profile.

## Proposed Commands

Command names are not implemented yet, but the intended UX is:

```text
another-brain model plan
another-brain model pull --kind embedding
another-brain model pull --kind memory
another-brain model status
another-brain model prune
```

`model plan` should show what would be downloaded, target cache path, model
revision, expected dimension, runtime precision, Redis vector dtype, license
warning if known, and estimated disk cost.

`model pull` should be explicit, resumable, and safe to rerun.

## Cache Contract

The model cache should be outside Redis and outside source control.

Recommended default:

```text
.cache/another-brain/models/
```

Production deployments should mount a persistent model cache volume separately
from Redis data.

Cached model metadata should record:

- provider;
- model name;
- revision or digest;
- local path;
- expected embedding dimension when applicable;
- model weight precision;
- embedding output precision;
- Redis vector dtype expected by this model profile;
- prompt/profile metadata when the model requires query/document prompts;
- download time;
- checksum or provider digest when available;
- license acknowledgement status when required.

## Startup Behavior

Startup should check:

- configured provider mode;
- model cache presence when local;
- embedding dimension compatibility with the active Redis index;
- network permission before any attempted download;
- model metadata consistency.

Health output should report model status without secrets:

- configured provider;
- model name;
- model revision/digest if safe;
- cache status;
- embedding dimension;
- download policy;
- whether network download is allowed.

## Required Classes

Add these to the directory/class architecture if this step is accepted:

`ModelInstallPolicy`

- Parsed policy for disabled/manual/lazy/on_start behavior.

`ModelRegistry`

- Resolves configured model names to provider-specific download metadata.

`ModelCache`

- Locates cached models and reads/writes model metadata.

`ModelInstaller`

- Performs explicit or policy-approved downloads.

`ModelStatus`

- Secret-free status object used by health and CLI output.

`ModelChecksumVerifier`

- Verifies downloaded files when checksum/digest data is available.

`ModelRuntimeProfile`

- Captures selected weight precision, output precision, vector dtype, device,
  and normalization behavior.

## Directory Impact

If accepted, add this package to the runtime tree:

```text
src/
  models/
    __init__.py
    policy.py
    registry.py
    cache.py
    installer.py
    status.py
    runtime.py
```

Do not put model download logic inside `src/memory/embeddings.py`; that module
should stay focused on embedding calls after a provider is ready.

## Review Decisions

Approve or change these before implementation:

1. No model download during package install.
2. Local model download requires explicit opt-in or a configured policy.
3. Default production policy is `manual`.
4. Local developer quickstart may use `lazy` only after opt-in.
5. Model cache is separate from Redis data.
6. Any launcher/packaging delegates model management to the server; it does
   not own model downloads.
7. Health output exposes model status but no API keys or signed URLs.
8. Embedding dimension mismatch blocks writes until migration/reindex is handled.
9. Redis vector dtype defaults to `FLOAT32`.
10. Q8/Q4 decisions apply to local model weights, not Redis vector storage.
11. Non-FLOAT32 embedding storage is postponed until recall and migration tests
    exist.
12. Harrier's default profile is SentenceTransformers + normalized 640-d output
    + Redis `FLOAT32` + cosine distance.

## Next Slice After Approval

Step 04 should define the memory record and Redis index contract:

- memory fields;
- Redis key format;
- RediSearch schema;
- TTL policy;
- migration/reindex rules.
