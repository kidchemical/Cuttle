# Detached Host Electron restart (UI only — Flask/daemon keep running).
# Use after electron/package.json bumps so the Host banner + CUTTLE_PACKAGE_VERSION
# match the mesh. Client machines use workers.self-update instead.
param(
    [string]$Repo = '',
    [switch]$NoPull,
    [string]$LogPath = ''
)

$ErrorActionPreference = 'Continue'
if (-not $Repo) {
    $Repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
}
if (-not $LogPath) {
    $LogPath = Join-Path $env:LOCALAPPDATA 'cuttle-desktop\host-electron-restart.log'
}
New-Item -ItemType Directory -Force -Path (Split-Path $LogPath) | Out-Null
function Log([string]$m) {
    $line = "$(Get-Date -Format o) $m"
    Add-Content -Path $LogPath -Value $line
    Write-Output $line
}

Log "host-electron-restart start repo=$Repo"

Start-Sleep -Seconds 1

Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
        $cmd = [string]$_.CommandLine
        if (-not $cmd) { return $false }
        if ($cmd -match 'cuttle_client_daemon|cuttle_device_worker|cuttle_daemon|web_chat_api') { return $false }
        if ($cmd -match 'Cuttle\.exe') { return $true }
        if ($cmd -match 'electron\.exe' -and $cmd -match [regex]::Escape($Repo)) { return $true }
        return $false
    } |
    ForEach-Object {
        Log "stopping pid=$($_.ProcessId)"
        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
    }

Start-Sleep -Seconds 2

if (-not $NoPull) {
    Push-Location $Repo
    try {
        Log "git pull --ff-only"
        & git pull --ff-only 2>&1 | ForEach-Object { Log $_ }
    } finally {
        Pop-Location
    }
}

$electronDir = Join-Path $Repo 'electron'
$exe = Join-Path $Repo 'electron\dist\win-unpacked\Cuttle.exe'
function Resolve-NpmCmd {
    $cmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -and (Test-Path -LiteralPath $cmd.Source)) {
        return [string]$cmd.Source
    }
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npm) { return $null }
    $src = [string]$npm.Source
    if ($src -match '\.cmd$') { return $src }
    $sibling = Join-Path (Split-Path -Parent $src) 'npm.cmd'
    if (Test-Path -LiteralPath $sibling) { return $sibling }
    return $null
}
$npmCmd = Resolve-NpmCmd

# Packaged Cuttle.exe bakes a stale version into app.asar — stamp the live
# checkout semver into the process env so titlebar/worker ads match mesh.
$pkgJson = Join-Path $electronDir 'package.json'
if (Test-Path -LiteralPath $pkgJson) {
    try {
        $pkg = Get-Content -LiteralPath $pkgJson -Raw | ConvertFrom-Json
        if ($pkg.version) {
            $env:CUTTLE_PACKAGE_VERSION = [string]$pkg.version
            Log "CUTTLE_PACKAGE_VERSION=$($env:CUTTLE_PACKAGE_VERSION)"
        }
    } catch {
        Log "WARN could not read package.json version: $_"
    }
}
$env:CUTTLE_HOSTED_BY_DAEMON = '1'

# Prefer source `npm start` on Host so main.js / package.json stay live.
# Packaged Cuttle.exe freezes version into app.asar at last electron-builder pack
# (that is why titlebar stuck at 0.2.15 after "restart").
if ($npmCmd -and (Test-Path $electronDir)) {
    Log "starting Host via $npmCmd (source checkout)"
    Start-Process -FilePath $npmCmd -ArgumentList @('start') -WorkingDirectory $electronDir
} elseif (Test-Path -LiteralPath $exe) {
    Log "starting $exe (Host packaged fallback)"
    Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe)
} else {
    Log "ERROR no Host Electron launch path (need npm.cmd or dist\\win-unpacked\\Cuttle.exe)"
    exit 3
}

Log "host-electron-restart done"
exit 0
