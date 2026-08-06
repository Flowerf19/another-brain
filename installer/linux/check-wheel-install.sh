#!/usr/bin/env bash
# TASK-041 clean-wheel gate: build sdist/wheel with `uv build --no-sources`,
# install the wheel into a throwaway venv, run `another-brain --help`, and fail
# if `another_brain` imports resolve from the checkout instead of the wheel.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "== build (uv build --no-sources) =="
rm -rf dist
uv build --no-sources --out-dir "$WORK/dist" >/dev/null
WHEEL="$(ls "$WORK"/dist/*.whl)"
SDIST="$(ls "$WORK"/dist/*.tar.gz)"
echo "built: $(basename "$WHEEL") + $(basename "$SDIST")"

echo "== install wheel into clean venv =="
uv venv --quiet "$WORK/venv"
uv pip install --quiet --python "$WORK/venv/bin/python" "$WHEEL"
echo "venv python: $("$WORK/venv/bin/python" --version 2>&1)"

BIN="$WORK/venv/bin/another-brain"
[ -x "$BIN" ] || { echo "FAIL: entry point $BIN missing"; exit 1; }

echo "== another-brain --help =="
"$BIN" --help >/dev/null || { echo "FAIL: --help exit non-zero"; exit 1; }

echo "== import provenance (wheel, not checkout) =="
# Run from $WORK, not the repo root: with the flat layout the checkout's
# another_brain/ would shadow the installed wheel via sys.path[0] == CWD.
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

echo "== bare command: typed missing-model error, stdout clean =="
OUT="$(BRAIN_DATA_DIR="$WORK/data" BRAIN_MODEL_CACHE_DIR="$WORK/models" \
  "$BIN" 2>"$WORK/stderr" || echo "exit=$?")"
[ "$OUT" = "exit=3" ] || { echo "FAIL: bare command expected exit 3, got $OUT"; exit 1; }
grep -q "model pull" "$WORK/stderr" || { echo "FAIL: missing model-not-installed error on stderr"; exit 1; }

# TASK-085 second pass: BRAIN_DISABLE_SQLITE_VEC=1 forces the NumPy vector
# fallback — startup must be unaffected, the typed missing-model error and
# exit 3 must still hold (the env var must not leak into the first pass).
echo "== bare command (BRAIN_DISABLE_SQLITE_VEC=1): typed missing-model error, stdout clean =="
OUT="$(BRAIN_DATA_DIR="$WORK/data" BRAIN_MODEL_CACHE_DIR="$WORK/models" \
  BRAIN_DISABLE_SQLITE_VEC=1 "$BIN" 2>"$WORK/stderr" || echo "exit=$?")"
[ "$OUT" = "exit=3" ] || { echo "FAIL: forced-fallback bare command expected exit 3, got $OUT"; exit 1; }
grep -q "model pull" "$WORK/stderr" || { echo "FAIL: forced-fallback pass missing model-not-installed error on stderr"; exit 1; }

echo "== sdist/wheel contents: no legacy flat src modules =="
"$WORK/venv/bin/python" - "$WHEEL" <<'PY'
import sys
import zipfile

names = zipfile.ZipFile(sys.argv[1]).namelist()
bad = [
    n for n in names
    if not n.startswith(("another_brain/", "another_brain-"))
    or n.endswith(".pyc")
]
if bad:
    print(f"FAIL: unexpected wheel entries: {bad}", file=sys.stderr)
    sys.exit(1)
print(f"ok: {len(names)} entries, another_brain only")
PY

echo "PASS: clean wheel install gate"
