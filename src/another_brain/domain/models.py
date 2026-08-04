"""Domain value types (embedding/storage phases)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class EmbeddingVector:
    """One validated FLOAT32 ``[640]`` unit-norm vector (input version 2).

    Produced only by the embedding provider after finite/unit-norm/shape
    validation; consumed by the storage layer as the canonical vector blob.
    """

    values: np.ndarray
