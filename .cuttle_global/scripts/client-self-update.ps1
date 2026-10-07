# Client deploy updater. ASCII only (Windows PowerShell 5.1 safe).
param(
    [Parameter(Mandatory = $true)][string]$Repo,
    [string]$HostName = '',
    [switch]$RestartElectron,
    [switch]$RestartDaemon,
    [switch]$NoElectron,
    [switch]$NoDaemon,
    [switch]$SkipPull,
    [string]$LogPath = ''
)

$ErrorActionPreference = 'Continue'
if (-not $LogPath) {
    $LogPath = Join-Path $env:LOCALAPPDATA 'cuttle-desktop\client-self-update.log'
}
New-Item -ItemType Directory -Force -Path (Split-Path $LogPath) | Out-Null
function Log([string]$m) {
    $line = "$(Get-Date -Format o) $m"
    Add-Content -Path $LogPath -Value $line
    Write-Output $line
}

$doElectron = -not $NoElectron
$doDaemon = -not $NoDaemon
Log "self-update start repo=$Repo host=$HostName electron=$doElectron daemon=$doDaemon skipPull=$SkipPull"
Start-Sleep -Seconds 2

if (-not (Test-Path -LiteralPath $Repo)) {
    Log "ERROR repo missing: $Repo"
    exit 2
}

$py = Join-Path $Repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) { $py = 'python' }
# SkipPull skips the completed merge, never the lifecycle safety recheck.
$helper = Join-Path $PSScriptRoot 'client-update-checkout.py'
$checkArgs = @('--repo', $Repo)
if ($SkipPull) { $checkArgs += '--check-only' }
$LASTEXITCODE = 1
try {
    & $py $helper @checkArgs 2>&1 | ForEach-Object { Log "$_" }
    if ($LASTEXITCODE -ne 0) { Log "ERROR checkout update refused"; exit 1 }
} catch { Log "ERROR checkout update refused: $_"; exit 1 }

# Stop client UI / sidecar / client-daemon only (never host flask daemon).
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $cmd = [string]$_.CommandLine
    if ($cmd -match 'cuttle_daemon\.py|web_chat_api') { return $false }
    $clientDaemon = [regex]::Escape((Join-Path $Repo 'src\scripts\cuttle_client_daemon.py'))
    $clientWorker = [regex]::Escape((Join-Path $Repo 'src\scripts\cuttle_device_worker.py'))
    $electronRoot = [regex]::Escape((Join-Path $Repo 'electron\'))
    $exe = [string]$_.ExecutablePath
    if ($doDaemon -and ($cmd -match $clientDaemon -or $cmd -match $clientWorker)) { return $true }
    if ($doElectron -and ($exe -match "^$electronRoot" -or $cmd -match $electronRoot)) { return $true }
    return $false
} | ForEach-Object {
    Log "stopping pid=$($_.ProcessId) name=$($_.Name)"
    try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
}
Start-Sleep -Seconds 3

$electronDir = Join-Path $Repo 'electron'

$pkgJson = Join-Path $electronDir 'package.json'
if (Test-Path -LiteralPath $pkgJson) {
    try {
        $pkg = Get-Content -LiteralPath $pkgJson -Raw | ConvertFrom-Json
        if ($pkg.version) {
            $env:CUTTLE_PACKAGE_VERSION = [string]$pkg.version
            Log "CUTTLE_PACKAGE_VERSION=$($env:CUTTLE_PACKAGE_VERSION)"
        }
    } catch {}
}

if ($doDaemon) {
    $daemonScript = Join-Path $Repo 'src\scripts\cuttle_client_daemon.py'
    if (Test-Path -LiteralPath $daemonScript) {
        Log "starting client-daemon"
        Start-Process -FilePath $py -ArgumentList @($daemonScript) -WorkingDirectory $Repo -WindowStyle Hidden
    }
}

if ($doElectron) {
    $started = $false
    $electronBin = Join-Path $electronDir 'node_modules\electron\dist\electron.exe'
    if (Test-Path -LiteralPath $electronBin) {
        $args = @('.')
        if ($HostName) { $args += "--host=$HostName" }
        Log "starting electron.exe"
        Start-Process -FilePath $electronBin -ArgumentList $args -WorkingDirectory $electronDir
        $started = $true
    }
    if (-not $started) {
        $npmCmd = $null
        $c = Get-Command npm.cmd -ErrorAction SilentlyContinue
        if ($c) { $npmCmd = [string]$c.Source }
        if ($npmCmd) {
            $line = if ($HostName) { "/c `"$npmCmd`" start -- --host=$HostName" } else { "/c `"$npmCmd`" start" }
            Log "starting via npm.cmd"
            Start-Process -FilePath 'cmd.exe' -ArgumentList $line -WorkingDirectory $electronDir
            $started = $true
        }
    }
    if (-not $started) {
        $exe = Join-Path $Repo 'electron\dist\win-unpacked\Cuttle.exe'
        if (Test-Path -LiteralPath $exe) {
            $args = @()
            if ($HostName) { $args += "--host=$HostName" }
            Log "starting packaged Cuttle.exe"
            Start-Process -FilePath $exe -ArgumentList $args -WorkingDirectory (Split-Path $exe)
            $started = $true
        }
    }
    if (-not $started) { Log "ERROR no Electron launch path" } else { Log "Electron start requested" }
}

try {
    Copy-Item -LiteralPath $LogPath -Destination (Join-Path $env:USERPROFILE 'Desktop\cuttle-self-update-last.log') -Force -ErrorAction SilentlyContinue
} catch {}
Log "self-update done"
exit 0
