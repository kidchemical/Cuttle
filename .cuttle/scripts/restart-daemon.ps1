# Restart Cuttle daemon once (loads restart watcher + coordinated Flask restart).
# Run from a standalone terminal — NOT from a Cursor agent hosted by Cuttle chat.
#
# Canonical paths (verified against cuttle_daemon.py + launcher.py):
#   WorkingDirectory = <repo root>            (PROJECT_ROOT; this script's ../..)
#   Script            = src\scripts\cuttle_daemon.py
# Daemon resolves SRC_ROOT / .env / logs via __file__, not cwd; cwd must still
# make the script path exist (do NOT use WD=src with ArgumentList src\scripts\...).
#
# Launcher/worker note: Windows venv starts a .venv\Scripts\python.exe launcher
# that spawns a Python311 worker with the same cmdline. Both match *cuttle_daemon*.
# taskkill /T on the launcher already tears down the worker. A follow-up kill of
# the worker prints "not found" on stderr; with $ErrorActionPreference=Stop that
# used to abort the script before Start-Process. Always swallow taskkill failures.

param(
  # When set, only validate discovery/paths/kill-loop safety — do not stop or start anything.
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$DaemonRel = 'src\scripts\cuttle_daemon.py'
$DaemonAbs = Join-Path $ProjectRoot $DaemonRel

if (-not (Test-Path -LiteralPath $VenvPython)) { throw "Missing venv python: $VenvPython" }
if (-not (Test-Path -LiteralPath $DaemonAbs)) { throw "Missing daemon script: $DaemonAbs" }

function Get-CuttleDaemonPids {
  $daemonProcs = @(
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
      Where-Object { $_.CommandLine -like '*cuttle_daemon*' }
  )
  # Unique: launcher + worker can share a tree but must never be listed twice.
  return @($daemonProcs | ForEach-Object { $_.ProcessId } | Select-Object -Unique)
}

function Stop-CuttleDaemonPids {
  param([int[]]$ProcessIds)
  foreach ($procId in $ProcessIds) {
    # Native stderr under Stop + 2>$null is a terminating error in Windows PowerShell.
    # Wrap via cmd so a post-/T "not found" cannot abort the restart.
    cmd /c "taskkill /F /T /PID $procId >nul 2>&1" | Out-Null
  }
}

Write-Host '=== BEFORE ==='
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*cuttle_daemon*' -or $_.CommandLine -like '*web_chat_api*' } |
  Select-Object ProcessId, ParentProcessId, CommandLine | Format-List

$ids = @(Get-CuttleDaemonPids)
if ($ids.Count -eq 0) {
  Write-Host 'No cuttle_daemon process found (already stopped?).'
} else {
  Write-Host ("Daemon PID(s) (unique): " + ($ids -join ', '))
}

if ($DryRun) {
  Write-Host '=== DRY RUN ==='
  Write-Host "ProjectRoot=$ProjectRoot"
  Write-Host "VenvPython=$VenvPython"
  Write-Host "DaemonRel=$DaemonRel"
  Write-Host "Start-Process WorkingDirectory=$ProjectRoot ArgumentList=$DaemonRel"
  # Prove the kill helper does not abort when PIDs are already gone (launcher/worker case).
  Stop-CuttleDaemonPids -ProcessIds @(1, 1)
  Write-Host 'kill_loop_survived_missing_pids=True'
  Write-Host 'DryRun complete — no processes stopped or started.'
  exit 0
}

# Stop daemon process tree (launcher + worker). Matches tray Exit ownership:
# daemon owns Flask/Discord children; /T tears the tree down.
if ($ids.Count -eq 0) {
  Write-Host 'Skipping stop (no daemon PIDs).'
} else {
  Write-Host ("Stopping daemon PID(s): " + ($ids -join ', '))
  Stop-CuttleDaemonPids -ProcessIds $ids
}

# Wait for port 8080 to release (Flask child may linger briefly)
$deadline = (Get-Date).AddSeconds(25)
while ((Get-Date) -lt $deadline) {
  $listeners = @(Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue)
  if (-not $listeners -or $listeners.Count -eq 0) { break }
  Start-Sleep -Seconds 1
}
$left = @(Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue)
if ($left.Count -gt 0) {
  Write-Host ("WARNING: port 8080 still held by PID(s): " + (($left | ForEach-Object OwningProcess) -join ', '))
} else {
  Write-Host 'Port 8080 is free.'
}

Write-Host 'Starting daemon...'
Start-Process -FilePath $VenvPython `
  -ArgumentList $DaemonRel `
  -WorkingDirectory $ProjectRoot `
  -WindowStyle Hidden

$ok = $false
for ($i = 0; $i -lt 60; $i++) {
  Start-Sleep -Seconds 1
  try {
    $code = & curl.exe -k -s -o NUL -w '%{http_code}' --max-time 3 https://127.0.0.1:8080/api/health
    if ($code -eq '200') { $ok = $true; break }
  } catch {}
}
Write-Host "health_ok=$ok"

Write-Host '=== AFTER ==='
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*cuttle_daemon*' -or $_.CommandLine -like '*web_chat_api*' } |
  Select-Object ProcessId, ParentProcessId, CommandLine | Format-List

Write-Host '=== /api/flask/restart/status ==='
& curl.exe -k -s --max-time 8 https://127.0.0.1:8080/api/flask/restart/status
Write-Host ''
Write-Host '=== /restart status (via chat API) ==='
$statusBody = '{"message":"/restart status","stream":false}'
& curl.exe -k -s --max-time 15 -X POST https://127.0.0.1:8080/api/chat `
  -H 'Content-Type: application/json' `
  --data-binary $statusBody
Write-Host ''
Write-Host 'Do NOT run /restart graceful yet — report these results first.'
