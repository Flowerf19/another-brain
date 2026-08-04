# Register the installed native stdio command on Windows.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("claude-code", "codex", "cursor", "gemini-cli")]
    [string[]]$Harness
)

$ErrorActionPreference = "Stop"

function Set-McpJson([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        $Data = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    } else {
        $Data = [PSCustomObject]@{}
    }
    if (-not $Data.PSObject.Properties["mcpServers"]) {
        $Data | Add-Member -MemberType NoteProperty -Name "mcpServers" -Value ([PSCustomObject]@{})
    }
    $Entry = [PSCustomObject]@{
        command = "another-brain"
        args = @()
    }
    $Data.mcpServers | Add-Member -MemberType NoteProperty -Name "another-brain" -Value $Entry -Force
    $Parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $Parent | Out-Null
    $Data | ConvertTo-Json -Depth 20 | Set-Content -Encoding utf8 -LiteralPath $Path
    Write-Host "Registered native stdio server in $Path"
}

foreach ($Name in $Harness) {
    switch ($Name) {
        "claude-code" {
            & claude mcp remove another-brain -s user 2>$null
            & claude mcp add another-brain -s user -- another-brain
        }
        "codex" { & codex mcp add another-brain -- another-brain }
        "cursor" { Set-McpJson (Join-Path $HOME ".cursor\mcp.json") }
        "gemini-cli" { Set-McpJson (Join-Path $HOME ".gemini\settings.json") }
    }
}
