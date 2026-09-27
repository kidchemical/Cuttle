# TRELLIS.2 needs Python 3.11 + Torch 2.8 cu128 wheels (cp311).
# Latest ComfyUI portable is Python 3.13 / Torch 2.13 and cannot load those wheels.
param(
    [string]$Root = "F:\AI\ComfyUI-Trellis",
    [string]$PortableTrellis = "F:\AI\ComfyUI_windows_portable\ComfyUI\custom_nodes\ComfyUI-Trellis2"
)

$ErrorActionPreference = "Stop"
$Py = "C:\Users\MainUser\AppData\Local\Programs\Python\Python311\python.exe"
if (-not (Test-Path $Py)) { throw "Python 3.11 not found at $Py" }

New-Item -ItemType Directory -Force -Path (Split-Path $Root -Parent) | Out-Null

if (-not (Test-Path (Join-Path $Root "main.py"))) {
    Write-Host "Cloning ComfyUI into $Root ..."
    git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git $Root
}

$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "Creating Python 3.11 venv..."
    & $Py -m venv (Join-Path $Root ".venv")
}

Write-Host "Installing PyTorch 2.8.0+cu128 (matches Trellis2 Windows wheels)..."
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
$ver = & $venvPy -c "import torch; print(torch.__version__, torch.cuda.is_available())"
Write-Host "torch: $ver"
if ($ver -notmatch "2\.8") { throw "Expected torch 2.8.x, got $ver" }

Write-Host "Installing ComfyUI requirements..."
& $venvPy -m pip install -r (Join-Path $Root "requirements.txt")

$nodes = Join-Path $Root "custom_nodes"
New-Item -ItemType Directory -Force -Path $nodes | Out-Null
$trellis = Join-Path $nodes "ComfyUI-Trellis2"
if (-not (Test-Path $trellis)) {
    if (Test-Path $PortableTrellis) {
        Write-Host "Copying Trellis2 nodes from portable install..."
        Copy-Item $PortableTrellis $trellis -Recurse
    } else {
        git clone --depth 1 https://github.com/visualbruno/ComfyUI-Trellis2.git $trellis
    }
}

$manager = Join-Path $nodes "ComfyUI-Manager"
if (-not (Test-Path $manager)) {
    git clone --depth 1 https://github.com/Comfy-Org/ComfyUI-Manager.git $manager
}

$wheelDir = Join-Path $trellis "wheels\Windows\Torch280"
Write-Host "Installing Trellis2 cp311 Torch280 wheels from $wheelDir"
Get-ChildItem $wheelDir -Filter "*-cp311-*.whl" | ForEach-Object {
    Write-Host "  $($_.Name)"
    & $venvPy -m pip install --no-warn-script-location $_.FullName
}

Write-Host "Installing Trellis2 Python requirements..."
& $venvPy -m pip install --no-warn-script-location meshlib requests pymeshlab opencv-python scipy plotly rembg huggingface_hub
& $venvPy -m pip install --no-warn-script-location open3d

$bat = Join-Path $Root "start_lowvram.bat"
@"
@echo off
cd /d "%~dp0"
.\.venv\Scripts\python.exe -s main.py --lowvram --disable-pinned-memory --preview-method auto --listen 127.0.0.1 --port 8188
"@ | Set-Content -Path $bat -Encoding ASCII

$wfSrc = Join-Path $trellis "example_workflows"
$wfDst = Join-Path $Root "user\default\workflows"
New-Item -ItemType Directory -Force -Path $wfDst, (Join-Path $Root "models\facebook"), (Join-Path $Root "models\microsoft"), (Join-Path $Root "input"), (Join-Path $Root "output") | Out-Null
if (Test-Path $wfSrc) { Copy-Item (Join-Path $wfSrc "*.json") $wfDst -Force }

Write-Host "DONE. Start with: $bat"
Write-Host "UI: http://127.0.0.1:8188"
