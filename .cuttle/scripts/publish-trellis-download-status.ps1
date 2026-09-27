# Mirror TRELLIS.2 download progress to a JSON file Flask already serves
# (/output/trellis-download-status.json) so chat cards can poll it.
param(
    [switch]$Once
)
$ErrorActionPreference = "Continue"
$Root = if ($env:COMFYUI_ROOT) { $env:COMFYUI_ROOT } else { Join-Path $env:USERPROFILE "ComfyUI-Trellis" }
$ms = Join-Path $Root "models\microsoft\TRELLIS.2-4B"
$txt = Join-Path $Root "models\trellis-download-status.txt"
$hubRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$hub = Join-Path $hubRoot "src\output\trellis-download-status.json"
New-Item -ItemType Directory -Force -Path (Split-Path $hub -Parent) | Out-Null

function Get-DownloaderRunning {
    $hit = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'download-trellis2-models|TRELLIS\.2-4B' }
    return [bool]$hit
}

function Publish-Status {
    $kv = @{}
    if (Test-Path $txt) {
        Get-Content $txt -ErrorAction SilentlyContinue | ForEach-Object {
            if ($_ -match '^([^=]+)=(.*)$') { $kv[$Matches[1]] = $Matches[2] }
        }
    }
    $st = @(Get-ChildItem (Join-Path $ms "ckpts") -Filter "*.safetensors" -ErrorAction SilentlyContinue)
    $bytes = 0L
    if ($st.Count) { $bytes = [int64]($st | Measure-Object Length -Sum).Sum }
    $current = 0
    $files = 0
    [void][int]::TryParse([string]$kv['current'], [ref]$current)
    [void][int]::TryParse([string]$kv['files'], [ref]$files)
    $state = [string]$kv['state']
    if ($state -eq 'trellis_ok') { $state = 'done' }
    $running = Get-DownloaderRunning
    if (-not $state) { $state = $(if ($running) { 'running' } else { 'unknown' }) }
    $percent = 0
    if ($state -eq 'done') { $percent = 100 }
    elseif ($files -gt 0) {
        $doneFiles = [Math]::Max(0, $current - 1)
        $percent = [int][Math]::Floor(($doneFiles / $files) * 100)
        if ($percent -gt 99) { $percent = 99 }
    }
    $file = [string]$kv['file']
    $label = if ($files -gt 0 -and $current -gt 0) {
        "{0}/{1} {2}" -f $current, $files, $file
    } elseif ($st.Count) {
        "{0} weight files on disk" -f $st.Count
    } else {
        "Waiting for Hugging Face…"
    }
    $obj = [ordered]@{
        state                 = $state
        percent               = $percent
        label                 = $label
        file                  = $file
        current               = $current
        files                 = $files
        complete_safetensors  = $st.Count
        complete_bytes        = $bytes
        downloader_running    = $running
        time                  = (Get-Date -Format o)
    }
    ($obj | ConvertTo-Json -Compress) | Set-Content -Path $hub -Encoding utf8
    return $obj
}

while ($true) {
    $snap = Publish-Status
    if ($Once) { $snap | ConvertTo-Json; break }
    $term = @('done', 'failed', 'needs_hf_login')
    if ($term -contains $snap.state -and -not $snap.downloader_running) { break }
    Start-Sleep -Seconds 4
}
