# Install ComfyUI portable + visualbruno TRELLIS.2 nodes on F:\AI
# Safe to re-run. Does not touch Cuttle's .venv.
param(
    [string]$Root = "F:\AI\ComfyUI_windows_portable",
    [string]$DownloadDir = "F:\AI\downloads",
    [string]$ReleaseUrl = "https://github.com/Comfy-Org/ComfyUI/releases/download/v0.33.1/ComfyUI_windows_portable_nvidia.7z",
    [switch]$SkipModels
)

$ErrorActionPreference = "Stop"
$SevenZip = "C:\Program Files\7-Zip\7z.exe"
$Archive = Join-Path $DownloadDir "ComfyUI_windows_portable_nvidia.7z"
$Parent = Split-Path $Root -Parent

New-Item -ItemType Directory -Force -Path $DownloadDir, $Parent | Out-Null

if (-not (Test-Path $Archive) -or (Get-Item $Archive).Length -lt 1GB) {
    Write-Host "Downloading ComfyUI portable (~2 GB)..."
    curl.exe -L --retry 5 --retry-all-errors -o $Archive $ReleaseUrl
    if ($LASTEXITCODE -ne 0) { throw "Download failed (exit $LASTEXITCODE)" }
}

$mainPy = Join-Path $Root "ComfyUI\main.py"
if (-not (Test-Path $mainPy)) {
    if (-not (Test-Path $SevenZip)) { throw "7-Zip not found at $SevenZip" }
    Write-Host "Extracting to $Parent ..."
    & $SevenZip x -y "-o$Parent" $Archive
    if ($LASTEXITCODE -ne 0) { throw "7z extract failed (exit $LASTEXITCODE)" }
}

if (-not (Test-Path $mainPy)) {
    throw "Extracted layout unexpected — missing $mainPy"
}

$py = Join-Path $Root "python_embeded\python.exe"
$nodes = Join-Path $Root "ComfyUI\custom_nodes"
New-Item -ItemType Directory -Force -Path $nodes | Out-Null

$manager = Join-Path $nodes "ComfyUI-Manager"
if (-not (Test-Path $manager)) {
    Write-Host "Cloning ComfyUI-Manager..."
    git clone --depth 1 https://github.com/Comfy-Org/ComfyUI-Manager.git $manager
}

$trellis = Join-Path $nodes "ComfyUI-Trellis2"
if (-not (Test-Path $trellis)) {
    Write-Host "Cloning ComfyUI-Trellis2..."
    git clone --depth 1 https://github.com/visualbruno/ComfyUI-Trellis2.git $trellis
} else {
    Write-Host "ComfyUI-Trellis2 already present — pulling..."
    git -C $trellis pull --ff-only
}

Write-Host "Detecting portable Python / Torch..."
$meta = & $py -c "import sys,torch; print(sys.version_info.major, sys.version_info.minor, torch.__version__)"
Write-Host "python/torch: $meta"
$parts = ($meta -split "\s+")
$pyMaj = $parts[0]
$pyMin = $parts[1]
$torchVer = $parts[2]
$cp = "cp$pyMaj$pyMin"
$torchFolder = "Torch280"
if ($torchVer -like "2.7*") { $torchFolder = "Torch270" }
elseif ($torchVer -like "2.10*" -or $torchVer -like "2.1*") { $torchFolder = "Torch2100" }
elseif ($torchVer -like "2.8*") { $torchFolder = "Torch280" }
Write-Host "Using wheels: $torchFolder / $cp"

$wheelDir = Join-Path $trellis "wheels\Windows\$torchFolder"
if (-not (Test-Path $wheelDir)) {
    Write-Host "WARN: $wheelDir missing — skipping prebuilt wheels (install.py may still work)"
} else {
    Get-ChildItem $wheelDir -Filter "*-$cp-*.whl" | ForEach-Object {
        Write-Host "Installing $($_.Name)"
        & $py -m pip install --no-warn-script-location $_.FullName
    }
    if (-not (Get-ChildItem $wheelDir -Filter "*-$cp-*.whl")) {
        Write-Host "WARN: no $cp wheels in $wheelDir"
        Get-ChildItem $wheelDir -Filter "*.whl" | Select-Object -ExpandProperty Name
    }
}

Write-Host "Installing Trellis2 requirements..."
& $py -m pip install --upgrade --no-warn-script-location pip
& $py -m pip install --no-warn-script-location -r (Join-Path $trellis "requirements.txt")
$installPy = Join-Path $trellis "install.py"
if (Test-Path $installPy) {
    Write-Host "Running install.py..."
    & $py $installPy
}

& $py -m pip install --no-warn-script-location huggingface_hub rembg

$lowvramBat = Join-Path $Root "run_nvidia_gpu_lowvram.bat"
@"
@echo off
.\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build --lowvram --disable-pinned-memory --preview-method auto --listen 127.0.0.1 --port 8188
"@ | Set-Content -Path $lowvramBat -Encoding ASCII

$wfSrc = Join-Path $trellis "example_workflows"
$wfDst = Join-Path $Root "ComfyUI\user\default\workflows"
New-Item -ItemType Directory -Force -Path $wfDst | Out-Null
if (Test-Path $wfSrc) {
    Copy-Item (Join-Path $wfSrc "*.json") $wfDst -Force
}

$modelsFb = Join-Path $Root "ComfyUI\models\facebook"
$modelsMs = Join-Path $Root "ComfyUI\models\microsoft"
New-Item -ItemType Directory -Force -Path $modelsFb, $modelsMs | Out-Null

if ($SkipModels) {
    Write-Host "Skipping model downloads (-SkipModels). TRELLIS.2-4B ~16GB + gated DINOv3 still needed."
} else {
    $trellisDir = Join-Path $modelsMs "TRELLIS.2-4B"
    $dinoDir = Join-Path $modelsFb "dinov3-vitl16-pretrain-lvd1689m"
    Write-Host "Downloading microsoft/TRELLIS.2-4B (this can take a while, ~16GB)..."
    & $py -c "from huggingface_hub import snapshot_download; snapshot_download('microsoft/TRELLIS.2-4B', local_dir=r'$trellisDir')"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARN: TRELLIS.2-4B download failed — retry later after huggingface-cli login"
    }

    Write-Host "Downloading facebook/dinov3-vitl16-pretrain-lvd1689m (GATED — accept the license first)..."
    & $py -c "from huggingface_hub import snapshot_download; snapshot_download('facebook/dinov3-vitl16-pretrain-lvd1689m', local_dir=r'$dinoDir')"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "WARN: DINOv3 is gated. Accept https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m then huggingface-cli login"
    }
}

Write-Host "DONE. Start with: $lowvramBat"
Write-Host "UI: http://127.0.0.1:8188"
