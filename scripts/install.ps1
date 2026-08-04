# Native Windows installer.
[CmdletBinding()]
param([switch]$SkipModel)

$ErrorActionPreference = "Stop"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required: https://docs.astral.sh/uv/getting-started/installation/"
}

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
uv tool install --force $ProjectRoot
if (-not $SkipModel) {
    another-brain model pull
} else {
    Write-Host "Model download skipped; run 'another-brain model pull' before first write/search."
}
another-brain doctor
Write-Host "Native install complete. Register the stdio command 'another-brain' in your MCP host."
