"""TASK-034: final protocols import with zero third-party dependencies and a
minimal fake satisfies every runtime-checkable Protocol."""
import subprocess
import sys
from pathlib import Path

import pytest

from another_brain.protocols import (
    GLOBAL_SCOPE_ID,
    AuditRepository,
    EmbeddingProvider,
    EmbeddingHealth,
    MemoryRepository,
    MemoryRetriever,
    MutationOutcome,
    Scope,
    ScopeKey,
)


def test_module_has_no_third_party_dependencies():
    # Isolated interpreter: after importing the contract module, no
    # third-party runtime family may be loaded.
    code = (
        "import sys, another_brain.protocols;"
        "bad = [m for m in ('redis', 'torch', 'sentence_transformers',"
        " 'onnxruntime', 'mcp', 'numpy') if m in sys.modules];"
        "assert not bad, bad"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=_src_path_env()
    )
    assert result.returncode == 0, result.stderr


def _src_path_env():
    import os

    env = dict(os.environ)
    src = str(Path(__file__).resolve().parents[2] / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    return env


class _Fake:
    """One object structurally satisfying all four protocols."""

    def store(self, record): pass
    def get(self, memory_id): return None
    def recent(self, scope, *, limit, filters=None): return []
    def reinforce(self, memory_id): return MutationOutcome.NOT_FOUND
    def soft_delete(self, memory_id): return MutationOutcome.NOT_FOUND
    def restore(self, memory_id): return MutationOutcome.NOT_FOUND
    def hard_delete(self, memory_id): return MutationOutcome.NOT_FOUND
    def search(self, *, query_text, query_vector, scope, filters=None): return []
    def record(self, event): pass
    def list_day(self, day): return []
    def embed_document(self, *, topic, summary): return None
    def embed_query(self, query): return None
    def health(self): return EmbeddingHealth.NOT_LOADED


@pytest.mark.parametrize(
    "protocol", [MemoryRepository, MemoryRetriever, AuditRepository, EmbeddingProvider]
)
def test_minimal_fake_satisfies_protocol(protocol):
    assert isinstance(_Fake(), protocol)


def test_scope_key_normalization():
    assert ScopeKey(Scope.GLOBAL, GLOBAL_SCOPE_ID).scope is Scope.GLOBAL
    with pytest.raises(ValueError):
        ScopeKey(Scope.GLOBAL, "proj-1")
    with pytest.raises(ValueError):
        ScopeKey(Scope.USER, "")


def test_mutation_outcome_shapes():
    assert {o.value for o in MutationOutcome} == {"applied", "not_found"}
