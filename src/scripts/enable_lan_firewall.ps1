# Run as Administrator. Allows Cuttle from phones on home LAN only (LocalSubnet).
$ErrorActionPreference = 'Stop'
Write-Host ''
Write-Host '=== Cuttle LAN Firewall Setup ===' -ForegroundColor Cyan
Write-Host ''

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host 'ERROR: Not running as Administrator.' -ForegroundColor Red
    Write-Host 'Right-click the shortcut and choose "Run as administrator".' -ForegroundColor Yellow
    Read-Host 'Press Enter to close'
    exit 1
}

# Remove legacy firewall rule on 8081 (that port is llama.cpp LLAMACPP_BASE_URL, not the web UI)
$oldHttp = Get-NetFirewallRule -DisplayName 'Cuttle LAN HTTP (LocalSubnet)' -ErrorAction SilentlyContinue
if ($oldHttp) {
    $pf = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $oldHttp -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pf -and $pf.LocalPort -eq 8081) {
        Remove-NetFirewallRule -DisplayName 'Cuttle LAN HTTP (LocalSubnet)' -ErrorAction SilentlyContinue
        Write-Host '[OK] Removed old firewall rule on port 8081 (llama.cpp API port)' -ForegroundColor Yellow
    }
}

$rules = @(
    @{ Name = 'Cuttle LAN HTTPS (LocalSubnet)'; Port = 8080; Remote = 'LocalSubnet' },
    @{ Name = 'Cuttle LAN HTTP (LocalSubnet)'; Port = 8888; Remote = 'LocalSubnet' },
    @{ Name = 'Cuttle LAN HTTP (Open LAN)'; Port = 8888; Remote = 'Any' },
    @{ Name = 'Cuttle LAN HTTP alt (8000)'; Port = 8000; Remote = 'Any' }
)
$ok = $true
foreach ($r in $rules) {
    $existing = Get-NetFirewallRule -DisplayName $r.Name -ErrorAction SilentlyContinue
    $needsCreate = $true
    if ($existing) {
        $pf = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $existing -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($pf -and [string]$pf.LocalPort -eq [string]$r.Port) {
            Write-Host "[OK] Already exists: $($r.Name) (port $($r.Port))" -ForegroundColor Green
            $needsCreate = $false
        } else {
            Remove-NetFirewallRule -DisplayName $r.Name -ErrorAction SilentlyContinue
            Write-Host "[..] Recreating $($r.Name) for port $($r.Port)" -ForegroundColor Yellow
        }
    }
    if ($needsCreate) {
        try {
            New-NetFirewallRule -DisplayName $r.Name -Direction Inbound -Protocol TCP -LocalPort $r.Port -Action Allow -Profile Private,Public -RemoteAddress $r.Remote | Out-Null
            Write-Host "[OK] Created: $($r.Name) (port $($r.Port), remote $($r.Remote))" -ForegroundColor Green
        } catch {
            Write-Host "[FAIL] $($r.Name): $_" -ForegroundColor Red
            $ok = $false
        }
    }
}

# Allow python.exe (Cuttle Flask) inbound on LAN HTTP port — some PCs need program rules too.
$pyPaths = @(
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    (Join-Path (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path '.venv\Scripts\python.exe')
)
foreach ($py in $pyPaths) {
    if (-not (Test-Path -LiteralPath $py)) { continue }
    $label = if ($py -match 'venv') { 'venv' } else { 'system' }
    $ruleName = "Cuttle Python LAN ($label)"
    $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "[OK] Already exists: $ruleName" -ForegroundColor Green
        continue
    }
    try {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
            -Program $py -Protocol TCP -LocalPort 8888,8080 `
            -Profile Private,Public | Out-Null
        Write-Host "[OK] Created program rule: $ruleName" -ForegroundColor Green
    } catch {
        Write-Host "[WARN] Program rule $ruleName : $_" -ForegroundColor Yellow
    }
}

try {
    Enable-NetFirewallRule -DisplayGroup 'Network Discovery' -ErrorAction SilentlyContinue | Out-Null
    Write-Host '[OK] Network Discovery firewall rules enabled' -ForegroundColor Green
} catch {
    Write-Host "[WARN] Network Discovery rules: $_" -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'Current rules:' -ForegroundColor Cyan
Get-NetFirewallRule -DisplayName 'Cuttle LAN*' -ErrorAction SilentlyContinue |
    Select-Object DisplayName, Enabled, Profile, Direction, Action |
    Format-Table -AutoSize

$wifi = Get-NetConnectionProfile | Where-Object { $_.InterfaceAlias -match 'Wi-Fi|WLAN' } | Select-Object -First 1
if ($wifi -and $wifi.NetworkCategory -eq 'Public') {
    Write-Host 'Note: Wi-Fi is PUBLIC — set to Private in Windows Settings for best results.' -ForegroundColor Yellow
    Write-Host '  Settings → Network & Internet → Wi-Fi → your network → Private network' -ForegroundColor Yellow
}

Write-Host ''
if ($ok) {
    Write-Host 'Done. Restart Cuttle, then on your phone open:' -ForegroundColor Green
    try {
        $ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -match '^192\.168\.' -or $_.IPAddress -match '^10\.' } | Select-Object -First 1).IPAddress
        if ($ip) {
            Write-Host "  https://${ip}:8888/phone" -ForegroundColor White
            Write-Host "  http://${ip}:8000/phone  (plain HTTP fallback)" -ForegroundColor Gray
        } else {
            Write-Host '  http://YOUR_PC_IP:8888/phone' -ForegroundColor White
        }
    } catch {
        Write-Host '  http://YOUR_PC_IP:8888/phone' -ForegroundColor White
    }
} else {
    Write-Host 'Some rules failed — read errors above.' -ForegroundColor Red
}

Write-Host ''
Read-Host 'Press Enter to close'
