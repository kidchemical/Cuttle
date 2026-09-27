# Snapshot of TRELLIS.2-4B download progress. Safe to run anytime; does not start/stop downloads.
$ErrorActionPreference = "Continue"
$Root = if ($env:COMFYUI_ROOT) { $env:COMFYUI_ROOT } else { "F:\AI\ComfyUI-Trellis" }
$ms = Join-Path $Root "models\microsoft\TRELLIS.2-4B"
$dino = Join-Path $Root "models\facebook\dinov3-vitl16-pretrain-lvd1689m"
$status = Join-Path $Root "models\trellis-download-status.txt"

Write-Host "=== status file ==="
if (Test-Path $status) { Get-Content $status } else { Write-Host "(none yet) $status" }

$alive = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'download-trellis2-models|TRELLIS\.2-4B' }
Write-Host "`n=== downloader process ==="
if ($alive) {
    $alive | ForEach-Object { Write-Host "RUNNING pid=$($_.ProcessId) parent=$($_.ParentProcessId)" }
} else {
    Write-Host "NOT RUNNING"
}

Write-Host "`n=== complete safetensors ==="
$done = @(Get-ChildItem (Join-Path $ms "ckpts") -Filter "*.safetensors" -ErrorAction SilentlyContinue)
if ($done.Count -eq 0) { Write-Host "(none)" } else {
    $done | Sort-Object Name | ForEach-Object { "{0,14:N0}  {1}" -f $_.Length, $_.Name }
    Write-Host ("complete_bytes={0:N0}  files={1}" -f (($done | Measure-Object Length -Sum).Sum), $done.Count)
}

$inc = @(Get-ChildItem $ms -Recurse -Filter "*.incomplete" -ErrorAction SilentlyContinue)
Write-Host "`n=== incomplete chunks ==="
if ($inc.Count -eq 0) { Write-Host "(none)" } else {
    Write-Host ("incomplete_bytes={0:N0}  files={1}  newest={2}" -f (($inc | Measure-Object Length -Sum).Sum), $inc.Count, (($inc | Sort-Object LastWriteTime | Select-Object -Last 1).LastWriteTime))
}

$dinoOk = Test-Path (Join-Path $dino "config.json")
Write-Host "`n=== dinov3 ==="
Write-Host ("present={0}  path={1}" -f $dinoOk, $dino)
