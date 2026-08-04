# Spike: fp32 oracle evaluation (TASK-001)

Isolated, evaluation-only project for the locked Q4-vs-fp32 quality/resource
evidence (Plan 07 GOAL-001). **Not** a root workspace member, not a root-lock
entry, not a wheel input. Torch/SentenceTransformers exist only inside this
directory and never enter the clean runtime.

- fp32 oracle: `microsoft/harrier-oss-v1-270m` @
  `31de22b673913c7d658c0f03f792d77c2dcf8ebd`
  (`model.safetensors` SHA-256 `90933b68…cb51a`), via SentenceTransformers on
  CPU with the profile's own pinned tokenizer.
- q4 target: `onnx-community/harrier-oss-v1-270m-ONNX` @
  `d59c919d0159aea2c19ed7d04288fcdd048d0f9c` (five pinned files, hash-verified),
  via raw `onnxruntime` CPU + `tokenizers`, direct `sentence_embedding`,
  query-only prompt — mirroring the final runtime contract.

```bash
cd spikes/fp32
uv sync
uv run python fetch_models.py     # download + SHA-256 verify both profiles
uv run python parity_probe.py     # smoke: paired cosine(q4, fp32) on probes
```

Model artifacts land in `.models/` (gitignored). Evidence manifests for the
real corpus gates (TASK-002..004) are emitted under `evidence/`.
