"""Download and SHA-256-verify both locked embedding profiles (TASK-001).

- fp32 oracle: microsoft/harrier-oss-v1-270m @ pinned revision; verifies the
  locked `model.safetensors` hash after snapshot download.
- q4 target: onnx-community/harrier-oss-v1-270m-ONNX @ pinned revision;
  downloads exactly the five locked runtime files and verifies each hash.

Artifacts land under `.models/`; reruns are idempotent (verified files are
kept, corrupt/missing files are re-downloaded).
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download

MODELS = Path(__file__).resolve().parent / ".models"

FP32_REPO = "microsoft/harrier-oss-v1-270m"
FP32_REVISION = "31de22b673913c7d658c0f03f792d77c2dcf8ebd"
FP32_SAFETENSORS_SHA256 = (
    "90933b6826b61afd9331e0ebe3c0598b421a32eda5fb301a114fe36f306cb51a"
)

Q4_REPO = "onnx-community/harrier-oss-v1-270m-ONNX"
Q4_REVISION = "d59c919d0159aea2c19ed7d04288fcdd048d0f9c"
Q4_FILES_SHA256 = {
    "onnx/model_q4.onnx": "228dca2603b907d673dd99cf89c309c0ca68baeed127416a5e027a48e62b0f49",
    "onnx/model_q4.onnx_data": "b5a15487360f5341659480ae4b5ad60028d5f865bd329196ec8d5708bbed3118",
    "config.json": "5366f9919a82aaeceb6707bf218c5769f414d60f5dbaf781fa07e5465487fd7c",
    "tokenizer.json": "ec95be298bea26f90370854faa650744c9fb0a04ca5e5ff95dd3913393ac5e45",
    "tokenizer_config.json": "135405f3479eaebc473e2e78593f2195c7598948a215ee748758def426b30f59",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check(path: Path, expected: str, label: str) -> bool:
    if not path.exists():
        print(f"MISSING {label}: {path}")
        return False
    actual = sha256(path)
    if actual != expected:
        print(f"HASH MISMATCH {label}: {actual} != {expected}")
        return False
    print(f"ok {label}: {path.name} ({path.stat().st_size / 1e6:.1f} MB)")
    return True


def fetch_q4() -> bool:
    dest = MODELS / "q4"
    ok = True
    for name, expected in Q4_FILES_SHA256.items():
        target = dest / name
        if not target.exists() or sha256(target) != expected:
            print(f"download q4 {name} ...")
            fetched = Path(
                hf_hub_download(
                    Q4_REPO, name, revision=Q4_REVISION, local_dir=str(dest)
                )
            )
            if fetched != target:
                target.parent.mkdir(parents=True, exist_ok=True)
                fetched.replace(target)
        ok &= check(target, expected, f"q4 {name}")
    return ok


def fetch_fp32() -> bool:
    dest = MODELS / "fp32"
    marker = dest / "model.safetensors"
    if not marker.exists() or sha256(marker) != FP32_SAFETENSORS_SHA256:
        print("download fp32 snapshot ...")
        snapshot_download(
            FP32_REPO, revision=FP32_REVISION, local_dir=str(dest),
            ignore_patterns=["*.bin", "onnx/*", "*.onnx", "*.onnx_data"],
        )
    return check(marker, FP32_SAFETENSORS_SHA256, "fp32 model.safetensors")


def main() -> int:
    ok = fetch_q4()
    ok = fetch_fp32() and ok
    if not ok:
        print("FAIL: model fetch/verify")
        return 1
    print("PASS: both profiles verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
