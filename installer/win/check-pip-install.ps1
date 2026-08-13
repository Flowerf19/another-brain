#Requires -Version 7.0
# TASK-006 pip-install gate (Windows): mirrors installer/linux/check-pip-install.sh
# step for step. Install the local checkout with standard pip (PEP 517
# hatchling build) into a throwaway venv created by `python -m venv`, run the
# installed `another-brain` console script, and prove import provenance from a
# NEUTRAL working directory (the checkout's root another_brain/ must not
# shadow the installed package). Standard-library tooling + pip only.
#
# Native exit codes never throw under $ErrorActionPreference='Stop', so every
# native call is followed by an explicit $LASTEXITCODE check.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Fail([string]$Message) {
    [Console]::Error.WriteLine("FAIL: $Message")
    exit 1
}

# requires-python is >=3.12: pick the first python3/python on PATH that
# qualifies (CI puts the matrix python there via setup-python).
$BasePy = $null
foreach ($Candidate in @('python3', 'python')) {
    $Resolved = Get-Command $Candidate -ErrorAction SilentlyContinue
    if (-not $Resolved) { continue }
    $Ver = & $Resolved.Source -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>$null
    if ($LASTEXITCODE -eq 0 -and $Ver) {
        $VerObj = [System.Version]$Ver
        if ($VerObj -ge [System.Version]'3.12') {
            $BasePy = $Resolved.Source
            break
        }
    }
}
if (-not $BasePy) { Fail 'no python >= 3.12 on PATH (requires-python)' }
Write-Output "base python: $(& $BasePy --version 2>&1)"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Push-Location $RepoRoot

$Work = Join-Path $env:TEMP ("pip-gate-" + [guid]::NewGuid().ToString('n'))
New-Item -ItemType Directory -Path $Work | Out-Null

try {
    Write-Output '== clean venv (python -m venv) =='
    & $BasePy -m venv (Join-Path $Work 'venv')
    if ($LASTEXITCODE -ne 0) { Fail "python -m venv exit $LASTEXITCODE" }
    $Py = Join-Path $Work 'venv\Scripts\python.exe'

    Write-Output '== pip install . (PEP 517 hatchling build from checkout) =='
    $env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
    try {
        & $Py -m pip install --quiet $RepoRoot
        if ($LASTEXITCODE -ne 0) { Fail "pip install exit $LASTEXITCODE" }
    } finally {
        Remove-Item Env:PIP_DISABLE_PIP_VERSION_CHECK -ErrorAction SilentlyContinue
    }
    Write-Output "venv python: $(& $Py --version 2>&1)"

    Write-Output '== pip show another-brain =='
    $Show = & $Py -m pip show another-brain
    if ($LASTEXITCODE -ne 0) { Fail 'pip show failed' }
    if (-not ($Show -contains 'Name: another-brain')) { Fail 'pip show: another-brain not installed' }

    $Bin = Join-Path $Work 'venv\Scripts\another-brain.exe'
    if (-not (Test-Path $Bin)) { Fail "entry point $Bin missing" }

    # Run from $Work, not the repo root: with the flat layout the checkout's
    # another_brain/ would shadow the installed package via sys.path[0] == CWD.
    Write-Output '== another-brain --version =='
    Push-Location $Work
    try {
        $VersionOut = & $Bin --version
        $Code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($Code -ne 0) { Fail "--version exit $Code" }
    if ($VersionOut -notmatch '^another-brain\s') { Fail "unexpected --version output: $VersionOut" }

    Write-Output '== another-brain --help =='
    Push-Location $Work
    try {
        & $Bin --help | Out-Null
        $Code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($Code -ne 0) { Fail "--help exit $Code" }

    Write-Output '== import provenance (venv, not checkout) =='
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

    Write-Output 'PASS: pip install gate'
} finally {
    Pop-Location
    Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue
}
