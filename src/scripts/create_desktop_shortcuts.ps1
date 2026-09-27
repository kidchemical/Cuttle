# Recreate Cuttle desktop shortcuts (run from repo: powershell -File src\scripts\create_desktop_shortcuts.ps1)
$desktop = [Environment]::GetFolderPath('Desktop')
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$icon = Join-Path $repo 'src\img\cuttle_logo.ico'
$shell = New-Object -ComObject WScript.Shell

function New-DesktopShortcut {
    param(
        [string]$Name,
        [string]$Target,
        [string]$Arguments = '',
        [string]$WorkingDirectory = $repo,
        [string]$Description = ''
    )
    $path = Join-Path $desktop "$Name.lnk"
    $sc = $shell.CreateShortcut($path)
    $sc.TargetPath = $Target
    if ($Arguments) { $sc.Arguments = $Arguments }
    $sc.WorkingDirectory = $WorkingDirectory
    if ($Description) { $sc.Description = $Description }
    $sc.WindowStyle = 1
    if (Test-Path -LiteralPath $icon) { $sc.IconLocation = "$icon,0" }
    $sc.Save()
    Write-Host "[OK] $path" -ForegroundColor Green
    return $path
}

New-DesktopShortcut -Name 'Enable Cuttle LAN Firewall' `
    -Target (Join-Path $repo 'src\scripts\enable_lan_firewall.bat') `
    -Description 'Allow Cuttle phone access from home Wi-Fi (run once; auto-elevates to admin)'

New-DesktopShortcut -Name 'Cuttle LAN Diagnostics' `
    -Target (Join-Path $repo 'src\scripts\diagnose_lan.bat') `
    -Description 'Show LAN IP, ports, and phone URLs for Cuttle'

New-DesktopShortcut -Name 'Start Cuttle' `
    -Target (Join-Path $repo '.venv\Scripts\python.exe') `
    -Arguments 'src\scripts\cuttle_daemon.py' `
    -Description 'Start Cuttle daemon, then open the desktop app (tray + Flask + Discord + cron)'

Write-Host ''
Write-Host 'Desktop shortcuts created.' -ForegroundColor Cyan
