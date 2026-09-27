# git.push action — same script as run_posix. Do not git push from the agent shell.
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$py = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    $py = 'python'
}
& $py (Join-Path $PSScriptRoot 'git-push.py')
exit $LASTEXITCODE
