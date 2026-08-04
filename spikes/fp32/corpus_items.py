"""Convert the embedding-quality-v1 corpus into the shared items format used
by q4_encode.py / fp32_encode.py: documents then queries, in corpus order.

Output: evidence/corpus_items.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parent


def main() -> int:
    corpus = json.loads((SPIKE / "corpus" / "embedding-quality-v1.json").read_text(encoding="utf-8"))
    items = [
        {"kind": "document", "id": d["doc_id"], "topic": d["topic"], "summary": d["summary"]}
        for d in corpus["documents"]
    ] + [
        {"kind": "query", "id": q["query_id"], "query": q["text"]}
        for q in corpus["queries"]
    ]
    out = SPIKE / "evidence" / "corpus_items.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"items": items}, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(items)} items -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
