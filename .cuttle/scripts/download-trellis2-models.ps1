# Resume Hugging Face weights for TRELLIS.2 (and optional gated DINOv3).
# Writes progress to F:\AI\ComfyUI-Trellis\models\trellis-download-status.txt
# so the agent can start this and disconnect. Poll with trellis-download-status.ps1.
param(
    [switch]$SkipDino
)
$ErrorActionPreference = "Stop"
$Root = if ($env:COMFYUI_ROOT) { $env:COMFYUI_ROOT } else { "F:\AI\ComfyUI-Trellis" }
$py = Join-Path $Root ".venv\Scripts\python.exe"
$ms = Join-Path $Root "models\microsoft\TRELLIS.2-4B"
$dino = Join-Path $Root "models\facebook\dinov3-vitl16-pretrain-lvd1689m"
$status = Join-Path $Root "models\trellis-download-status.txt"
New-Item -ItemType Directory -Force -Path $ms, (Split-Path $dino -Parent) | Out-Null
Get-ChildItem $ms -Recurse -Filter "*.lock" -ErrorAction SilentlyContinue | Remove-Item -Force

Set-Content -Path $status -Value "state=starting`npid=$PID`ntime=$(Get-Date -Format o)`nrepo=microsoft/TRELLIS.2-4B`n"
Write-Host "Resuming microsoft/TRELLIS.2-4B into $ms (one file at a time)"
Write-Host "Status file: $status"
$env:PYTHONUNBUFFERED = "1"
$env:TRELLIS_STATUS = $status
& $py -u -c @"
import os, time
from huggingface_hub import hf_hub_download, list_repo_files
repo = 'microsoft/TRELLIS.2-4B'
local = r'$ms'
status_path = r'$status'
files = list_repo_files(repo)

def write_status(**kw):
    lines = [f'{k}={v}' for k, v in kw.items()]
    with open(status_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')

write_status(state='running', repo=repo, files=len(files), current='0', file='listing', time=time.strftime('%Y-%m-%dT%H:%M:%S'))
print('files', len(files), flush=True)
for i, name in enumerate(files, 1):
    write_status(state='running', repo=repo, files=len(files), current=i, file=name, time=time.strftime('%Y-%m-%dT%H:%M:%S'))
    print(f'[{i}/{len(files)}] {name}', flush=True)
    hf_hub_download(repo_id=repo, filename=name, local_dir=local)
    print(f'  done {name}', flush=True)
write_status(state='trellis_ok', repo=repo, files=len(files), current=len(files), file='complete', time=time.strftime('%Y-%m-%dT%H:%M:%S'))
print('TRELLIS ok', local, flush=True)
"@
if ($LASTEXITCODE -ne 0) {
    Add-Content -Path $status -Value "state=failed`nexit=$LASTEXITCODE`ntime=$(Get-Date -Format o)"
    throw "TRELLIS.2-4B download failed"
}

if ($SkipDino) {
    Add-Content -Path $status -Value "dino=skipped`nstate=done`ntime=$(Get-Date -Format o)"
    Write-Host "Skipping DINOv3"
    exit 0
}
Write-Host "Downloading gated facebook/dinov3-vitl16-pretrain-lvd1689m (requires HF login + license accept)"
& $py -c "from huggingface_hub import snapshot_download; p=snapshot_download('facebook/dinov3-vitl16-pretrain-lvd1689m', local_dir=r'$dino', resume_download=True); print('DINOv3 ok', p)"
if ($LASTEXITCODE -ne 0) {
    Add-Content -Path $status -Value "dino=failed`nstate=needs_hf_login`ntime=$(Get-Date -Format o)"
    Write-Host "DINOv3 failed. Accept https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m then huggingface-cli login"
    exit 2
}
Add-Content -Path $status -Value "dino=ok`nstate=done`ntime=$(Get-Date -Format o)"
