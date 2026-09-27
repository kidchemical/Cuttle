# Soft-delete idle Cuttle chat sessions (cuttle_auth.db).
# Params (optional): hours, dry_run, include_starred  → CUTTLE_PARAM_*
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path (Join-Path $root '.cuttle\scripts\cleanup-idle-sessions.py'))) {
    $root = (Get-Location).Path
}
$py = Join-Path $root '.venv\Scripts\python.exe'
$script = Join-Path $root '.cuttle\scripts\cleanup-idle-sessions.py'
if (-not (Test-Path $py)) { Write-Output "python not found: $py"; exit 1 }
if (-not (Test-Path $script)) { Write-Output "script not found: $script"; exit 1 }

$hours = @($env:CUTTLE_PARAM_HOURS, '24') | Where-Object { $_ } | Select-Object -First 1
$argsList = @($script, '--hours', "$hours")

$dry = @($env:CUTTLE_PARAM_DRY_RUN, '') | Select-Object -First 1
if ($dry -and @('1', 'true', 'yes', 'on') -contains $dry.Trim().ToLower()) {
    $argsList += '--dry-run'
}
$star = @($env:CUTTLE_PARAM_INCLUDE_STARRED, '') | Select-Object -First 1
if ($star -and @('1', 'true', 'yes', 'on') -contains $star.Trim().ToLower()) {
    $argsList += '--include-starred'
}

& $py @argsList
exit $LASTEXITCODE
