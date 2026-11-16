<#
.SYNOPSIS
    Install the Virtual Display Driver (VDD) unattended and add N virtual monitors.

.DESCRIPTION
    Windows-only, needs an elevated shell (GitHub-hosted runners are elevated).

    Follows VDD's own "Community Scripts/silent-install.ps1"
    (github.com/VirtualDrivers/Virtual-Display-Driver):
      1. write C:\VirtualDisplayDriver\vdd_settings.xml first; the driver reads
         the monitor count from it when the device is created,
      2. trust the SignPath publisher certificates embedded in mttvdd.cat,
      3. create the Root\MttVDD device with nefcon.
    Both downloads are pinned by version and SHA256.

    Afterwards run tools/ci/set_display_layout.py to arrange the monitors.

.EXAMPLE
    pwsh tools/ci/install_virtual_display_driver.ps1 -Count 2
#>
[CmdletBinding()]
param(
    [ValidateRange(1, 8)]
    [int]$Count = 2,

    # Modes the virtual monitors offer; the layout script picks one per monitor.
    [string[]]$Resolutions = @('1280x720', '1920x1080', '2560x1440', '3840x2160'),

    [string]$WorkDir = (Join-Path ([IO.Path]::GetTempPath()) 'vdd-install'),

    [int]$WaitSeconds = 60
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Pinned downloads (bump version + hash together).
$DriverUrl = 'https://github.com/VirtualDrivers/Virtual-Display-Driver/releases/download/25.7.23/VirtualDisplayDriver-x86.Driver.Only.zip'
$DriverSha256 = 'e24210692b442b39af763536330ce78b423f19342b7a7792c26de3944e418b3a'
$NefconUrl = 'https://github.com/nefarius/nefcon/releases/download/v1.14.0/nefcon_v1.14.0.zip'
$NefconSha256 = 'a15557da24a9efca203158de3b43b0eaf982db231f0194031f1ed428bc13e669'
$ConfigDir = 'C:\VirtualDisplayDriver'

function Assert-Admin {
    $principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this script from an elevated shell.'
    }
}

function Get-PinnedFile([string]$Url, [string]$Sha256, [string]$Path) {
    Write-Host "Downloading $Url"
    Invoke-WebRequest -Uri $Url -OutFile $Path -UseBasicParsing
    $actual = (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLowerInvariant()
    if ($actual -ne $Sha256) {
        throw "SHA256 mismatch for $Url`n  expected $Sha256`n  actual   $actual"
    }
}

function Write-VddSettings([int]$MonitorCount, [string[]]$Modes) {
    $resolutionXml = foreach ($mode in $Modes) {
        $w, $h = $mode -split 'x'
        "        <resolution><width>$w</width><height>$h</height><refresh_rate>60</refresh_rate></resolution>"
    }
    # Mirrors the defaults of VDD's shipped vdd_settings.xml; only count and
    # resolutions differ. Sections not listed keep the driver's built-in defaults.
    $xml = @"
<?xml version='1.0' encoding='utf-8'?>
<vdd_settings>
    <monitors>
        <count>$MonitorCount</count>
    </monitors>
    <gpu>
        <friendlyname>default</friendlyname>
    </gpu>
    <global>
        <g_refresh_rate>60</g_refresh_rate>
    </global>
    <resolutions>
$($resolutionXml -join "`n")
    </resolutions>
    <logging>
        <SendLogsThroughPipe>true</SendLogsThroughPipe>
        <logging>false</logging>
        <debuglogging>false</debuglogging>
    </logging>
    <colour>
        <SDR10bit>false</SDR10bit>
        <HDRPlus>false</HDRPlus>
        <ColourFormat>RGB</ColourFormat>
    </colour>
    <cursor>
        <HardwareCursor>true</HardwareCursor>
        <CursorMaxX>128</CursorMaxX>
        <CursorMaxY>128</CursorMaxY>
        <AlphaCursorSupport>true</AlphaCursorSupport>
        <XorCursorSupportLevel>2</XorCursorSupportLevel>
    </cursor>
    <edid>
        <CustomEdid>false</CustomEdid>
        <PreventSpoof>false</PreventSpoof>
        <EdidCeaOverride>false</EdidCeaOverride>
    </edid>
</vdd_settings>
"@
    New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
    Set-Content -Path (Join-Path $ConfigDir 'vdd_settings.xml') -Value $xml -Encoding UTF8
    Write-Host "Wrote $ConfigDir\vdd_settings.xml ($MonitorCount monitors)"
}

function Get-VirtualMonitorCount {
    @(Get-PnpDevice -Class Monitor -PresentOnly -ErrorAction SilentlyContinue |
        Where-Object { $_.InstanceId -match 'MTT' -or $_.FriendlyName -match 'VDD' }).Count
}

Assert-Admin
New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null

Write-Host '--- Displays before install'
Get-PnpDevice -Class Display, Monitor -PresentOnly | Format-Table -AutoSize Class, FriendlyName, Status, InstanceId | Out-String -Width 200 | Write-Host

Write-VddSettings -MonitorCount $Count -Modes $Resolutions

$driverZip = Join-Path $WorkDir 'vdd.zip'
$nefconZip = Join-Path $WorkDir 'nefcon.zip'
Get-PinnedFile -Url $DriverUrl -Sha256 $DriverSha256 -Path $driverZip
Get-PinnedFile -Url $NefconUrl -Sha256 $NefconSha256 -Path $nefconZip
Expand-Archive -Path $driverZip -DestinationPath (Join-Path $WorkDir 'vdd') -Force
Expand-Archive -Path $nefconZip -DestinationPath (Join-Path $WorkDir 'nefcon') -Force

$inf = Get-ChildItem -Path (Join-Path $WorkDir 'vdd') -Recurse -Filter 'MttVDD.inf' | Select-Object -First 1
$cat = Get-ChildItem -Path (Join-Path $WorkDir 'vdd') -Recurse -Filter 'mttvdd.cat' | Select-Object -First 1
$nefcon = Get-ChildItem -Path (Join-Path $WorkDir 'nefcon') -Recurse -Filter 'nefconw.exe' |
    Where-Object { $_.DirectoryName -match 'x64' } | Select-Object -First 1
if (-not $inf -or -not $cat -or -not $nefcon) {
    throw "Driver package layout changed: inf=$inf cat=$cat nefconw=$nefcon"
}

# Trust the driver publisher so PnP installs without a prompt.
$certs = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2Collection
$certs.Import([IO.File]::ReadAllBytes($cat.FullName))
foreach ($cert in $certs) {
    $cerPath = Join-Path $WorkDir "$($cert.Thumbprint).cer"
    [IO.File]::WriteAllBytes($cerPath, $cert.Export([Security.Cryptography.X509Certificates.X509ContentType]::Cert))
    Import-Certificate -FilePath $cerPath -CertStoreLocation 'Cert:\LocalMachine\TrustedPublisher' | Out-Null
    Write-Host "Trusted publisher cert: $($cert.Subject)"
}

Write-Host "Installing $($inf.FullName)"
$proc = Start-Process -FilePath $nefcon.FullName -ArgumentList @('install', "`"$($inf.FullName)`"", 'Root\MttVDD') -Wait -PassThru -NoNewWindow
Write-Host "nefconw exit code: $($proc.ExitCode)"
if ($proc.ExitCode -ne 0 -and $proc.ExitCode -ne 3010) {  # 3010 = success, reboot suggested
    throw "nefconw install failed with exit code $($proc.ExitCode)"
}

$deadline = (Get-Date).AddSeconds($WaitSeconds)
do {
    Start-Sleep -Seconds 2
    $found = Get-VirtualMonitorCount
} while ($found -lt $Count -and (Get-Date) -lt $deadline)

Write-Host '--- Displays after install'
Get-PnpDevice -Class Display, Monitor -PresentOnly | Format-Table -AutoSize Class, FriendlyName, Status, InstanceId | Out-String -Width 200 | Write-Host

# The PnP name match is a heuristic; set_display_layout.py does the real check
# (it fails with a full device dump if the monitors are missing).
if ($found -lt $Count) {
    Write-Warning "Expected $Count virtual monitors, PnP shows $found after $WaitSeconds s."
} else {
    Write-Host "Virtual Display Driver ready with $found monitor(s)."
}
