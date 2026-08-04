"""Encode probe/corpus payloads with the pinned q4 ONNX profile.

Run from the REPO ROOT environment so the gate measures the exact production
stack (onnxruntime 1.28.x, tokenizers 0.23.1):

    cd <repo root>
    uv run python spikes/fp32/q4_encode.py [probes.json] [out.npy]

Payloads are input version 2: documents are `topic.replace("-"," ") + "\n" +
summary.strip()` with no prompt; queries are `QUERY_PROMPT + query.strip()`.
The graph returns normalized `sentence_embedding [batch, 640]` directly.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

SPIKE = Path(__file__).resolve().parent
Q4_DIR = SPIKE / ".models" / "q4"
sys.path.insert(0, str(SPIKE))
from payloads import QUERY_PROMPT, document_payload, query_payload  # noqa: E402,F401


def load() -> tuple[Tokenizer, ort.InferenceSession]:
    tokenizer = Tokenizer.from_file(str(Q4_DIR / "tokenizer.json"))
    session = ort.InferenceSession(
        str(Q4_DIR / "onnx" / "model_q4.onnx"),
        providers=["CPUExecutionProvider"],
    )
    return tokenizer, session


def embed_texts(tokenizer: Tokenizer, session: ort.InferenceSession, texts: list[str]) -> np.ndarray:
    input_names = {i.name for i in session.get_inputs()}
    encodings = tokenizer.encode_batch(texts)
    max_len = max(len(e.ids) for e in encodings)

    def padded(values: list[list[int]], pad: int) -> np.ndarray:
        return np.array(
            [v + [pad] * (max_len - len(v)) for v in values], dtype=np.int64
        )

    feed = {}
    if "input_ids" in input_names:
        feed["input_ids"] = padded([e.ids for e in encodings], 0)
    if "attention_mask" in input_names:
        feed["attention_mask"] = padded([e.attention_mask for e in encodings], 0)
    if "token_type_ids" in input_names:
        feed["token_type_ids"] = padded([e.type_ids for e in encodings], 0)
    outputs = session.run(["sentence_embedding"], feed)[0]
    assert outputs.dtype == np.float32 and outputs.ndim == 2
    return outputs


def embed(texts: list[str]) -> np.ndarray:
    tokenizer, session = load()
    return embed_texts(tokenizer, session, texts)


def main() -> int:
    probes_path = Path(sys.argv[1]) if len(sys.argv) > 1 else SPIKE / "probes.json"
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else SPIKE / "evidence" / "q4_probe.npy"
    probes = json.loads(probes_path.read_text(encoding="utf-8"))["items"]
    texts = [
        document_payload(p["topic"], p["summary"]) if p["kind"] == "document"
        else query_payload(p["query"])
        for p in probes
    ]
    vectors = embed(texts)
    norms = np.linalg.norm(vectors, axis=1)
    assert np.all(np.isfinite(vectors)), "non-finite q4 output"
    assert np.allclose(norms, 1.0, atol=1e-5), f"q4 outputs not unit norm: {norms}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(out_path, vectors)
    print(f"q4: wrote {vectors.shape} -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
