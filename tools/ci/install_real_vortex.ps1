<#
.SYNOPSIS
  Installs a Vortex release and the rig extension that opens its free-download
  dialog. Used by .github/workflows/real-vortex.yml.

.DESCRIPTION
  Downloads vortex-setup-<Version>.exe from the Nexus-Mods/Vortex GitHub
  release, checks it against -Sha256 when one is given, installs it silently,
  and copies tools/ci/real_vortex into Vortex's user extension folder.

  -Version latest resolves to the newest stable release at run time. That
  installer cannot be pinned, so no hash is checked for it.

  Writes to GITHUB_OUTPUT: "exe" (path of Vortex.exe), "version" (the release
  installed) and "sha256" (of the installer).

  No Nexus account is involved and nothing is downloaded from Nexus Mods.
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Sha256 = "",
    [string]$WorkDir = (Join-Path $env:RUNNER_TEMP "vortex")
)

$ErrorActionPreference = "Stop"
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

if ($Version -eq "latest") {
    $tag = gh release view --repo Nexus-Mods/Vortex --json tagName --jq .tagName
    if ($LASTEXITCODE -ne 0 -or -not $tag) { throw "could not resolve the latest Vortex release" }
    $Version = $tag.TrimStart("v")
    Write-Host "Latest stable Vortex release: $Version"
}

Write-Host "Downloading Vortex $Version"
gh release download "v$Version" --repo Nexus-Mods/Vortex --pattern "vortex-setup-$Version.exe" --dir $WorkDir --clobber
if ($LASTEXITCODE -ne 0) { throw "download failed" }
$installer = Join-Path $WorkDir "vortex-setup-$Version.exe"
$actual = (Get-FileHash $installer -Algorithm SHA256).Hash.ToLower()
Write-Host "Installer SHA256: $actual"
if ($Sha256 -and ($actual -ne $Sha256.ToLower())) {
    throw "installer hash mismatch: expected $Sha256"
}
# Visible on the run page: which installer ran, and whether it was pinned.
$pin = if ($Sha256) { "matches the pinned hash" } else { "not pinned" }
Write-Host "::notice title=Vortex installer::vortex-setup-$Version.exe, SHA256 $actual ($pin)"

Write-Host "Installing silently"
$install = Start-Process -FilePath $installer -ArgumentList "/S" -PassThru
if (-not $install.WaitForExit(600000)) {
    $install.Kill()
    throw "installer still running after 10 minutes"
}
Write-Host "Installer exit code: $($install.ExitCode)"

$roots = @("C:\Program Files", "C:\Program Files (x86)", (Join-Path $env:LOCALAPPDATA "Programs"))
$exe = $roots | Where-Object { Test-Path $_ } | ForEach-Object {
    Get-ChildItem -Path $_ -Filter "Vortex.exe" -Recurse -Depth 4 -ErrorAction SilentlyContinue
} | Select-Object -First 1
if (-not $exe) { throw "Vortex.exe not found after install" }
Write-Host "Vortex.exe: $($exe.FullName)"

$plugins = Join-Path $env:APPDATA "Vortex\plugins\rig-free-dialog"
New-Item -ItemType Directory -Force -Path $plugins | Out-Null
Copy-Item -Path (Join-Path $PSScriptRoot "real_vortex\*") -Destination $plugins -Force
Write-Host "Extension copied to $plugins"

if ($env:GITHUB_OUTPUT) {
    "exe=$($exe.FullName)" | Out-File -FilePath $env:GITHUB_OUTPUT -Append -Encoding utf8
    "version=$Version" | Out-File -FilePath $env:GITHUB_OUTPUT -Append -Encoding utf8
    "sha256=$actual" | Out-File -FilePath $env:GITHUB_OUTPUT -Append -Encoding utf8
}
