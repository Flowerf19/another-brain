#Requires -Version 7.0
# TASK-083 clean-wheel gate (Windows): mirrors installer/linux/check-wheel-install.sh
# step for step. Build sdist/wheel with `uv build --no-sources`, install the
# wheel into a throwaway venv, prove import provenance from a NEUTRAL working
# directory (the checkout's root another_brain/ must not shadow the wheel),
# and verify the bare command's typed missing-model contract (exit 3, stderr
# message, stdout protocol-clean).
#
# Native exit codes never throw under $ErrorActionPreference='Stop', so every
# native call is followed by an explicit $LASTEXITCODE check.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Fail([string]$Message) {
    [Console]::Error.WriteLine("FAIL: $Message")
    exit 1
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Push-Location $RepoRoot

$Work = Join-Path $env:TEMP ("wheel-gate-" + [guid]::NewGuid().ToString('n'))
New-Item -ItemType Directory -Path $Work | Out-Null

try {
    Write-Output '== build (uv build --no-sources) =='
    Remove-Item -Recurse -Force 'dist' -ErrorAction SilentlyContinue
    uv build --no-sources --out-dir (Join-Path $Work 'dist') | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "uv build exit $LASTEXITCODE" }
    $Wheel = (Get-ChildItem (Join-Path $Work 'dist') -Filter '*.whl' | Select-Object -First 1).FullName
    $Sdist = (Get-ChildItem (Join-Path $Work 'dist') -Filter '*.tar.gz' | Select-Object -First 1).FullName
    if (-not $Wheel -or -not $Sdist) { Fail 'build produced no wheel/sdist' }
    Write-Output "built: $(Split-Path $Wheel -Leaf) + $(Split-Path $Sdist -Leaf)"

    Write-Output '== install wheel into clean venv =='
    $Venv = Join-Path $Work 'venv'
    uv venv --quiet $Venv
    if ($LASTEXITCODE -ne 0) { Fail "uv venv exit $LASTEXITCODE" }
    $Py = Join-Path $Venv 'Scripts\python.exe'
    uv pip install --quiet --python $Py $Wheel
    if ($LASTEXITCODE -ne 0) { Fail "uv pip install exit $LASTEXITCODE" }
    Write-Output "venv python: $(& $Py --version 2>&1)"

    $Bin = Join-Path $Venv 'Scripts\another-brain.exe'
    if (-not (Test-Path $Bin)) { Fail "entry point $Bin missing" }

    Write-Output '== another-brain --help =='
    & $Bin --help | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail '--help exit non-zero' }

    Write-Output '== import provenance (wheel, not checkout) =='
    # Run from $Work, not the repo root: with the flat layout the checkout's
    # another_brain/ would shadow the installed wheel via sys.path[0] == CWD.
    $provenance = @'
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
'@
    Push-Location $Work
    try {
        $provenance | & $Py - $RepoRoot
        if ($LASTEXITCODE -ne 0) { Fail 'import provenance check failed' }
    } finally {
        Pop-Location
    }

    Write-Output '== bare command: typed missing-model error, stdout clean =='
    $stderrFile = Join-Path $Work 'stderr.txt'
    $env:BRAIN_DATA_DIR = Join-Path $Work 'data'
    $env:BRAIN_MODEL_CACHE_DIR = Join-Path $Work 'models'
    try {
        $stdout = & $Bin 2>$stderrFile
        $code = $LASTEXITCODE
    } finally {
        Remove-Item Env:BRAIN_DATA_DIR -ErrorAction SilentlyContinue
        Remove-Item Env:BRAIN_MODEL_CACHE_DIR -ErrorAction SilentlyContinue
    }
    if ($code -ne 3) {
        [Console]::Error.WriteLine("bare command expected exit 3, got $code --- captured stderr:")
        [Console]::Error.WriteLine((Get-Content $stderrFile -Raw))
        exit 1
    }
    if ($stdout) { Fail "bare command polluted stdout: $stdout" }
    if (-not (Select-String -Path $stderrFile -Pattern 'model pull' -Quiet)) {
        Fail 'missing model-not-installed error on stderr'
    }

    # TASK-085 second pass: BRAIN_DISABLE_SQLITE_VEC=1 forces the NumPy
    # vector fallback; startup must be unaffected and the typed
    # missing-model error + exit 3 must still hold.
    Write-Output '== bare command (BRAIN_DISABLE_SQLITE_VEC=1): typed missing-model error, stdout clean =='
    $env:BRAIN_DATA_DIR = Join-Path $Work 'data'
    $env:BRAIN_MODEL_CACHE_DIR = Join-Path $Work 'models'
    $env:BRAIN_DISABLE_SQLITE_VEC = '1'
    try {
        $stdout = & $Bin 2>$stderrFile
        $code = $LASTEXITCODE
    } finally {
        Remove-Item Env:BRAIN_DATA_DIR -ErrorAction SilentlyContinue
        Remove-Item Env:BRAIN_MODEL_CACHE_DIR -ErrorAction SilentlyContinue
        Remove-Item Env:BRAIN_DISABLE_SQLITE_VEC -ErrorAction SilentlyContinue
    }
    if ($code -ne 3) { Fail "forced-fallback bare command expected exit 3, got $code" }
    if ($stdout) { Fail "forced-fallback bare command polluted stdout: $stdout" }
    if (-not (Select-String -Path $stderrFile -Pattern 'model pull' -Quiet)) {
        Fail 'forced-fallback pass missing model-not-installed error on stderr'
    }

    Write-Output '== sdist/wheel contents: no legacy flat src modules =='
    $contents = @'
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
'@
    $contents | & $Py - $Wheel
    if ($LASTEXITCODE -ne 0) { Fail 'wheel contents check failed' }

    Write-Output 'PASS: clean wheel install gate'
} finally {
    Pop-Location
    Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
}
