#!/usr/bin/env bash
# TASK-006 pip-install gate: install the local checkout with standard pip
# (PEP 517 hatchling build) into a throwaway venv created by `python -m venv`,
# run the installed `another-brain` console script, and fail if
# `another_brain` imports resolve from the checkout instead of the venv.
# Standard-library tooling + pip only.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# requires-python is >=3.12: pick the first python3/python on PATH that
# qualifies (CI puts the matrix python there via setup-python).
BASE_PY=""
for CAND in python3 python; do
    command -v "$CAND" >/dev/null 2>&1 || continue
    PYVER="$("$CAND" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || true)"
    [ -n "$PYVER" ] || continue
    if [ "$(printf '%s\n3.12\n' "$PYVER" | sort -V | head -n1)" = "3.12" ]; then
        BASE_PY="$(command -v "$CAND")"
        break
    fi
done
[ -n "$BASE_PY" ] || { echo "FAIL: no python >= 3.12 on PATH (requires-python)"; exit 1; }
echo "base python: $("$BASE_PY" --version 2>&1)"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "== clean venv (python -m venv) =="
"$BASE_PY" -m venv "$WORK/venv"

echo "== pip install . (PEP 517 hatchling build from checkout) =="
PIP_DISABLE_PIP_VERSION_CHECK=1 "$WORK/venv/bin/python" -m pip install --quiet "$REPO_ROOT"
echo "venv python: $("$WORK/venv/bin/python" --version 2>&1)"

echo "== pip show another-brain =="
SHOW_OUT="$("$WORK/venv/bin/python" -m pip show another-brain)"
echo "$SHOW_OUT" | grep -q "^Name: another-brain$" \
  || { echo "FAIL: pip show: another-brain not installed"; exit 1; }

BIN="$WORK/venv/bin/another-brain"
[ -x "$BIN" ] || { echo "FAIL: entry point $BIN missing"; exit 1; }

# Run from $WORK, not the repo root: with the flat layout the checkout's
# another_brain/ would shadow the installed package via sys.path[0] == CWD.
echo "== another-brain --version =="
VERSION_OUT="$(cd "$WORK" && "$BIN" --version)"
case "$VERSION_OUT" in
    another-brain\ *) echo "$VERSION_OUT" ;;
    *) echo "FAIL: unexpected --version output: $VERSION_OUT"; exit 1 ;;
esac

echo "== another-brain --help =="
(cd "$WORK" && "$BIN" --help >/dev/null) || { echo "FAIL: --help exit non-zero"; exit 1; }

echo "== import provenance (venv, not checkout) =="
(cd "$WORK" && "$WORK/venv/bin/python" - "$REPO_ROOT") <<'PY'
import pathlib
import sys

import another_brain

repo = pathlib.Path(sys.argv[1]).resolve()
module = pathlib.Path(another_brain.__file__).resolve()
if module.is_relative_to(repo):
    print(f"FAIL: another_brain resolves from checkout: {module}", file=sys.stderr)
    sys.exit(1)
if not module.is_relative_to(pathlib.Path(sys.prefix).resolve()):
    print(f"FAIL: another_brain outside the venv: {module}", file=sys.stderr)
    sys.exit(1)
print(f"ok: {module}")
PY

echo "PASS: pip install gate"
