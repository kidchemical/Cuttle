param(
    [switch]$Browser,
    [switch]$NoLaunch,
    [switch]$LaunchOnly
)

$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
$venv = Join-Path $repo '.venv\Scripts\python.exe'
$setup = Join-Path $repo 'src\scripts\setup_cuttle.py'
$setupArgs = @()
if ($Browser) { $setupArgs += '--browser' }
if ($NoLaunch) { $setupArgs += '--no-launch' }
if ($LaunchOnly) { $setupArgs += '--launch-only' }

if (Test-Path $venv) {
    & $venv $setup @setupArgs
} elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
    & py.exe -3 $setup @setupArgs
} elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
    & python.exe $setup @setupArgs
} else {
    Write-Host 'Python 3.11+ is required. Install 64-bit Python from https://www.python.org/downloads/windows/ and reopen PowerShell.'
    exit 1
}
exit $LASTEXITCODE
