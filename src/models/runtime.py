"""ModelRuntimeProfile — weight/embedding precision, Redis vector dtype,
device, normalization (Step 03).

Two separate decisions (Step 03 "Embedding Precision And Quantization"):
weight_precision is how the local model *runs*; output_precision is what gets
written to Redis. MVP locks output to float32/FLOAT32; Q8/Q4 weights are
postponed until a recall benchmark exists (decisions 10-11).
"""
from __future__ import annotations

from dataclasses import dataclass

WEIGHT_PRECISIONS = frozenset({"auto", "fp32", "fp16", "bf16"})
POSTPONED_WEIGHT_PRECISIONS = frozenset({"int8", "q8", "q4"})

_TORCH_DTYPE_BY_PRECISION = {
    "fp32": "float32",
    "fp16": "float16",
    "bf16": "bfloat16",
}


@dataclass(frozen=True)
class ModelRuntimeProfile:
    weight_precision: str = "auto"       # auto | fp32 | fp16 | bf16
    output_precision: str = "float32"    # locked in MVP
    vector_dtype: str = "FLOAT32"        # locked in MVP
    device: str = "auto"                 # auto | cpu | cuda | mps
    normalize: bool = True
    query_prompt_name: str | None = None

    def torch_dtype_name(self) -> str | None:
        """Torch dtype for model loading; None means let the runtime pick
        (fp16/bf16 on accelerators, fp32 on CPU)."""
        return _TORCH_DTYPE_BY_PRECISION.get(self.weight_precision)
