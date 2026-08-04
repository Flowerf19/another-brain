"""Input-version-2 payload construction shared by both encode paths.

Documents: `topic.replace("-", " ") + "\\n" + summary.strip()` (no prompt).
Queries: `QUERY_PROMPT + query.strip()`. Byte-exactness here is what makes the
q4/fp32 parity measurement meaningful.

The templates live once, in the product (`another_brain.payloads`, locked by
the TASK-042 manifest); this spike module re-exports them so the evaluation
pipeline can never drift from what the installed product encodes.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from another_brain.payloads import (  # noqa: E402,F401
    QUERY_PROMPT,  # noqa: F401 - re-exported for spike callers
    document_payload,
    query_payload,
)
