# Agent-agnostic workers platform CLI bridge for .cuttle_global/actions.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
$src = Join-Path $root 'src'
$env:PYTHONPATH = $src

$forward = @($args)
$verb = $env:CUTTLE_PARAM_VERB
if (-not $verb -and $forward.Count -gt 0) {
    $verb = [string]$forward[0]
    if ($forward.Count -gt 1) { $forward = @($forward[1..($forward.Count - 1)]) } else { $forward = @() }
}
if (-not $verb) {
    Write-Output '{"success":false,"error":"missing verb (list|submit|status|cancel|wait|plan|blender-shard|batch-status|batch-watch|self-update)"}'
    exit 1
}
& $py -m api.device_workers.cli $verb @forward
exit $LASTEXITCODE
