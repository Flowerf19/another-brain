# TASK-001 evidence notes (2026-08-04)

## Verified profiles

- fp32 oracle `microsoft/harrier-oss-v1-270m` @ `31de22b673913c7d658c0f03f792d77c2dcf8ebd`;
  `model.safetensors` 536.2 MB, SHA-256 matches the locked
  `90933b68…cb51a`. Runs in THIS env: torch 2.13.0+cpu, sentence-transformers
  5.6.1, transformers 5.14.1, tokenizers 0.22.2.
- q4 target `onnx-community/harrier-oss-v1-270m-ONNX` @ `d59c919d0159aea2c19ed7d04288fcdd048d0f9c`;
  all five locked files downloaded and SHA-256-verified (`model_q4.onnx_data`
  205.5 MB). Runs in the ROOT env: onnxruntime 1.28.0, tokenizers 0.23.1 —
  the exact production stack.

## Environment split (deviation from "one project", recorded)

transformers 5.x caps `tokenizers<=0.23.0` and tokenizers 0.23.0 was never
released, so no single env can host SentenceTransformers AND the production
tokenizers 0.23.1. Consequences:

- the fp32 oracle runs in this spike env (tokenizers 0.22.2);
- the q4 target runs under the root project lock (`uv run python
  spikes/fp32/q4_encode.py`), preserving production parity;
- tokenizer *artifacts* are hash-pinned per profile, so measured parity still
  includes tokenizer-conversion differences.

## Payload byte-exactness

`config_sentence_transformers.json` has `default_prompt_name: null`, so the
fp32 path applies no implicit prompt. Both sides prepend the locked
`QUERY_PROMPT` manually for queries and use unprompted
`topic.replace("-"," ") + "\n" + summary.strip()` for documents — payloads are
byte-identical (verified via shared `payloads.py`). The fp32 repo's
`web_search_query` prompt string equals the locked QUERY_PROMPT.

## Smoke parity (8 probes, NOT the gate corpus)

Paired cosine(q4, fp32): median **0.9806**, min 0.9785 (5 documents VI+EN,
3 queries). The q4 graph output is FLOAT32 [batch,640], finite, unit-norm
(asserted at encode time).

**Risk signal for TASK-004:** the gate requires corpus median >=0.99 and
p5 >=0.97. The smoke median 0.9806 is below 0.99. 8 probes are not the
600-doc corpus, but if corpus-level parity lands at ~0.98, TASK-004 forces a
stop or an approved plan revision (e.g., a different quantization variant) —
it must not silently lower the gate.
