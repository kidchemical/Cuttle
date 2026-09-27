# Start ComfyUI portable in low-VRAM mode (RTX 3080 10GB).
$ErrorActionPreference = "Stop"
$Root = if ($env:COMFYUI_ROOT) { $env:COMFYUI_ROOT } else { "F:\AI\ComfyUI-Trellis" }
$bat = Join-Path $Root "start_lowvram.bat"
if (-not (Test-Path $bat)) { $bat = Join-Path $Root "run_nvidia_gpu_lowvram.bat" }
if (-not (Test-Path $bat)) { $bat = Join-Path $Root "run_nvidia_gpu.bat" }
if (-not (Test-Path $bat)) { throw "ComfyUI not installed at $Root" }
try {
    $r = Invoke-WebRequest "http://127.0.0.1:8188/system_stats" -UseBasicParsing -TimeoutSec 3
    Write-Output ("Already running HTTP " + [int]$r.StatusCode + " http://127.0.0.1:8188")
    exit 0
} catch {}
Start-Process -FilePath $bat -WorkingDirectory $Root
Write-Output "Launching ComfyUI from $bat — UI at http://127.0.0.1:8188"
