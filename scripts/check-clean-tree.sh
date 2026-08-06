#!/usr/bin/env bash
# TASK-080/081 clean-tree gate: fail if forbidden dependency families appear
# in the locked runtime graph, or if Redis/Docker/Torch runtime references
# appear in source, tests, scripts, product docs, pyproject, or workflows.
#
# Allowed mentions (explicit, grep-able): negations documenting their absence
# ("no Docker", "Redis ... not part", "no Torch") and historical/superseded
# context inside .agents/ (not scanned). Everything else fails the gate.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

FAILED=0

echo "== TASK-080: forbidden families in locked runtime dependency graph =="
GRAPH="$(uv tree --locked --no-dev)"
FORBIDDEN_DEPS='redis|torch|sentence-transformers|nvidia|cuda|lancedb|duckdb|docker'
if echo "$GRAPH" | grep -Eiq "$FORBIDDEN_DEPS"; then
    echo "FAIL: forbidden packages in runtime graph:"
    echo "$GRAPH" | grep -Ei "$FORBIDDEN_DEPS"
    FAILED=1
else
    echo "ok: $(echo "$GRAPH" | grep -c ' v') packages, no forbidden families"
fi

echo "== TASK-080: runtime deps limited to the locked set =="
ALLOWED='^(another-brain|mcp|onnxruntime|tokenizers|numpy|platformdirs|sqlite-vec|filelock) v'
TOP="$(echo "$GRAPH" | grep -E '^[a-z0-9._-]+ v' || true)"
if echo "$TOP" | grep -Ev "$ALLOWED" | grep -q .; then
    echo "FAIL: unexpected direct runtime dependencies:"
    echo "$TOP" | grep -Ev "$ALLOWED"
    FAILED=1
else
    echo "ok: direct runtime deps match the locked list"
fi

echo "== TASK-081: zero Redis/Docker/Torch references in the clean tree =="
# Scan code, tests, scripts, product docs, pyproject, and workflows.
# .agents/ is intentionally excluded (plans + superseded history live there).
# CHANGELOG.md is excluded for the same reason as the legacy-baseline fixture
# below: release notes must be able to say what 0.11.0 REMOVED, and naming
# Redis/Docker/Torch there is the point, not a leak.
# tests/fixtures/legacy-baseline/ is excluded for the same reason: the TASK-031
# oracle export is a *record of* the legacy stack, not legacy code running here.
# It names Redis/Docker/Torch because that is what was measured; scrubbing those
# names would destroy the evidence. Data only — no importable module lives there.
# scripts/release-rehearsal.sh is excluded alongside this file: both are
# gates, and a gate has to name the families it forbids in order to assert
# their absence. A grep cannot tell an assertion from a leak.
PATTERN='redis|docker|torch|sentence[_-]transformers|fastmcp|compose'
# Allowed: explicit negations of legacy families, and the import-guard tuples
# in tests that assert those modules never load at startup.
ALLOW='no Docker, container|Docker and Redis are not part|no Torch|never imports? Redis/Torch/ST|.redis., .torch., .sentence_transformers.'
HITS="$(grep -rEni "$PATTERN" \
    another_brain/ tests/ scripts/ installer/ docs/ skills/ README.md pyproject.toml .github/ 2>/dev/null \
    | grep -v __pycache__ \
    | grep -v 'scripts/check-clean-tree.sh' \
    | grep -v 'scripts/release-rehearsal.sh' \
    | grep -v '^tests/fixtures/legacy-baseline/' \
    | grep -Ev "$ALLOW" || true)"
if [ -n "$HITS" ]; then
    echo "FAIL: forbidden references found:"
    echo "$HITS"
    FAILED=1
else
    echo "ok: no forbidden references"
fi

if [ "$FAILED" -ne 0 ]; then
    echo "FAIL: clean-tree gate"
    exit 1
fi
echo "PASS: clean-tree gate"
