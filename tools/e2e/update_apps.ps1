<#
.SYNOPSIS
    Upgrade the apps NexusAutoDL drives to their latest versions and record them.

.DESCRIPTION
    Run on the test VM (tools/e2e/RUNBOOK.md) before tools/e2e/run_e2e.py, so a
    run always tests the newest Vortex, browsers and Wabbajack. Writes the
    installed versions to e2e-results\<timestamp>\versions.json.

.EXAMPLE
    pwsh tools/e2e/update_apps.ps1
#>
[CmdletBinding()]
param(
    [string[]]$Packages = @('NexusMods.Vortex', 'Google.Chrome', 'Mozilla.Firefox', 'WabbajackTools.Wabbajack'),
    [string]$OutDir = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "e2e-results\$(Get-Date -Format 'yyyyMMdd-HHmmss')")
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw 'winget not found. Install "App Installer" from the Microsoft Store first.'
}

function Get-InstalledVersion([string]$Id) {
    # winget prints a table; the row for the package holds: Name  Id  Version  [Available]  Source
    $lines = winget list --id $Id --exact --disable-interactivity --accept-source-agreements 2>$null
    $row = $lines | Where-Object { $_ -match [regex]::Escape($Id) } | Select-Object -Last 1
    if (-not $row) { return $null }
    $cols = ($row -split '\s{2,}') | Where-Object { $_ }
    $idx = [array]::IndexOf($cols, ($cols | Where-Object { $_ -eq $Id } | Select-Object -First 1))
    if ($idx -ge 0 -and $idx + 1 -lt $cols.Count) { return $cols[$idx + 1] }
    return $row.Trim()
}

$versions = [ordered]@{}
foreach ($id in $Packages) {
    $before = Get-InstalledVersion $id
    if (-not $before) {
        Write-Host "$id is not installed; installing"
        winget install --id $id --exact --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    } else {
        Write-Host "$id $before; upgrading if newer"
        # Exit code is non-zero when no upgrade is available; that is fine.
        winget upgrade --id $id --exact --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    }
    $after = Get-InstalledVersion $id
    $versions[$id] = [ordered]@{ before = $before; after = $after }
    Write-Host "  -> $after"
}

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$path = Join-Path $OutDir 'versions.json'
$versions | ConvertTo-Json -Depth 3 | Set-Content -Path $path -Encoding UTF8
Write-Host "Wrote $path"
