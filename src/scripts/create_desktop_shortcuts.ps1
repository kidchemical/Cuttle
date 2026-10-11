# Cuttle desktop shortcuts — two primary launchers + a daemon-only launcher.
#   Cuttle Host    (red mascot)   — daemon + app on this PC
#   Cuttle Client  (mascot)       — connect to a Host
#   Cuttle Daemon  (mascot)       — daemon only, no app
#
# Run from the repo:  powershell -File src\scripts\create_desktop_shortcuts.ps1
# Electron calls this on startup (idempotent) so installs self-heal.
param(
    [switch]$Quiet
)
$ErrorActionPreference = 'Continue'
$desktop = [Environment]::GetFolderPath('Desktop')
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$launcher = Join-Path $repo '.cuttle\scripts\start-cuttle.cmd'
$iconClient = Join-Path $repo 'src\img\cuttle_logo.ico'
$iconHost = Join-Path $repo 'src\img\cuttle_logo_host.ico'
if (-not (Test-Path -LiteralPath $iconHost)) { $iconHost = $iconClient }
if (-not (Test-Path -LiteralPath $launcher)) {
    if (-not $Quiet) { Write-Host "Launcher not found: $launcher" -ForegroundColor Red }
    exit 1
}
$shell = New-Object -ComObject WScript.Shell

function New-CuttleShortcut {
    param([string]$Name, [string]$Arguments, [string]$Icon, [string]$Description)
    $path = Join-Path $desktop "$Name.lnk"
    $sc = $shell.CreateShortcut($path)
    $sc.TargetPath = $launcher
    $sc.Arguments = $Arguments
    $sc.WorkingDirectory = (Split-Path $launcher)
    $sc.WindowStyle = 1
    $sc.Description = $Description
    if (Test-Path -LiteralPath $Icon) { $sc.IconLocation = "$Icon,0" }
    $sc.Save()
    if (-not $Quiet) { Write-Host "[OK] $path" -ForegroundColor Green }
}

New-CuttleShortcut -Name 'Cuttle Host' -Arguments 'host' -Icon $iconHost `
    -Description 'Start Cuttle Host (daemon + app on this PC)'
New-CuttleShortcut -Name 'Cuttle Client' -Arguments 'client' -Icon $iconClient `
    -Description 'Start Cuttle Client (connect to a Host)'
New-CuttleShortcut -Name 'Cuttle Daemon' -Arguments 'daemon' -Icon $iconClient `
    -Description 'Start the Cuttle daemon only (no app)'

# Remove legacy shortcut names this replaces (never touch unrelated files).
foreach ($name in @('Cuttle.lnk', 'Start Cuttle.lnk', 'Start-Cuttle-Client.cmd', 'Cuttle.cmd')) {
    $legacy = Join-Path $desktop $name
    if (Test-Path -LiteralPath $legacy) {
        Remove-Item -LiteralPath $legacy -Force -ErrorAction SilentlyContinue
        if (-not $Quiet) { Write-Host "[del] $legacy" -ForegroundColor DarkGray }
    }
}

if (-not $Quiet) {
    Write-Host ''
    Write-Host 'Desktop shortcuts: Cuttle Host / Cuttle Client / Cuttle Daemon' -ForegroundColor Cyan
}
