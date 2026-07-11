"""ModelCache — cached local model lookup and metadata (Step 03).

Layout: <MODEL_CACHE_DIR>/<name with '/' -> '--'>/ holding the model snapshot
plus a meta.json written only after a fully successful pull. meta.json is the
"download completed" marker: a dir without it is treated as absent, so an
interrupted download can never be mistaken for an installed model.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

META_FILENAME = "meta.json"


def _dir_name(model_name: str) -> str:
    return model_name.replace("/", "--")


class ModelCache:
    def __init__(self, root: Path | str):
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def model_dir(self, model_name: str) -> Path:
        return self._root / _dir_name(model_name)

    def is_cached(self, model_name: str) -> bool:
        return (self.model_dir(model_name) / META_FILENAME).is_file()

    def read_meta(self, model_name: str) -> dict[str, Any] | None:
        path = self.model_dir(model_name) / META_FILENAME
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write_meta(self, model_name: str, meta: dict[str, Any]) -> None:
        model_dir = self.model_dir(model_name)
        model_dir.mkdir(parents=True, exist_ok=True)
        path = model_dir / META_FILENAME
        path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
