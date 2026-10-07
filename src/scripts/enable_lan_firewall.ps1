# Run as Administrator. Lets devices on this PC's own subnet reach Cuttle.
#
# Every rule is LocalSubnet + Private profile only: never "Any" remote address
# and never the Public profile, so a laptop on cafe/hotel Wi-Fi stays closed.
# Re-running replaces every earlier "Cuttle LAN*" / "Cuttle Python LAN*" rule,
# including old releases' open (Any-address) rules.
#
# Ports: -HttpsPort/-HttpPort/-PhonePort, else CUTTLE_HTTPS_PORT /
# CUTTLE_HTTP_PORT / CUTTLE_PHONE_HTTPS_PORT from src\.env, else 8080/8000/8888.
param(
    [int]$HttpsPort = 0,
    [int]$HttpPort = 0,
    [int]$PhonePort = 0
)
$ErrorActionPreference = 'Stop'
Write-Host ''
Write-Host '=== Cuttle LAN Firewall Setup (local subnet, Private networks only) ===' -ForegroundColor Cyan
Write-Host ''

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host 'ERROR: Not running as Administrator.' -ForegroundColor Red
    Write-Host 'Right-click the shortcut and choose "Run as administrator".' -ForegroundColor Yellow
    Read-Host 'Press Enter to close'
    exit 1
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$envValues = @{}
$envFile = Join-Path $repo 'src\.env'
if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile -Encoding UTF8) {
        if ($line -match '^\s*(CUTTLE_(HTTPS|HTTP|PHONE_HTTPS)_PORT)\s*=\s*"?(\d+)"?\s*$') {
            $envValues[$Matches[1]] = [int]$Matches[3]
        }
    }
}
function Resolve-Port([int]$given, [string]$name, [int]$default) {
    if ($given -gt 0) { return $given }
    if ($envValues.ContainsKey($name)) { return $envValues[$name] }
    return $default
}
$HttpsPort = Resolve-Port $HttpsPort 'CUTTLE_HTTPS_PORT' 8080
$HttpPort = Resolve-Port $HttpPort 'CUTTLE_HTTP_PORT' 8000
$PhonePort = Resolve-Port $PhonePort 'CUTTLE_PHONE_HTTPS_PORT' 8888
$ports = @($HttpsPort, $HttpPort, $PhonePort) | Select-Object -Unique

# Replace, never extend: earlier versions created Any-address rules.
foreach ($pattern in @('Cuttle LAN*', 'Cuttle Python LAN*')) {
    $old = @(Get-NetFirewallRule -DisplayName $pattern -ErrorAction SilentlyContinue)
    foreach ($rule in $old) {
        Remove-NetFirewallRule -Name $rule.Name -ErrorAction SilentlyContinue
        Write-Host "[..] Removed old rule: $($rule.DisplayName)" -ForegroundColor Yellow
    }
}

$ok = $true
$rules = @(
    @{ Name = 'Cuttle LAN HTTPS'; Port = $HttpsPort },
    @{ Name = 'Cuttle LAN HTTP'; Port = $HttpPort },
    @{ Name = 'Cuttle LAN Phone HTTPS'; Port = $PhonePort }
)
foreach ($r in $rules) {
    try {
        New-NetFirewallRule -DisplayName $r.Name -Direction Inbound -Protocol TCP `
            -LocalPort $r.Port -Action Allow -Profile Private -RemoteAddress LocalSubnet | Out-Null
        Write-Host "[OK] $($r.Name): TCP $($r.Port) from LocalSubnet (Private networks)" -ForegroundColor Green
    } catch {
        Write-Host "[FAIL] $($r.Name): $_" -ForegroundColor Red
        $ok = $false
    }
}

# Some PCs also need a program rule for the venv interpreter; same scope.
$py = Join-Path $repo '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $py) {
    try {
        New-NetFirewallRule -DisplayName 'Cuttle Python LAN (venv)' -Direction Inbound -Action Allow `
            -Program $py -Protocol TCP -LocalPort $ports `
            -Profile Private -RemoteAddress LocalSubnet | Out-Null
        Write-Host '[OK] Cuttle Python LAN (venv): LocalSubnet (Private networks)' -ForegroundColor Green
    } catch {
        Write-Host "[WARN] Program rule: $_" -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host 'Current rules:' -ForegroundColor Cyan
Get-NetFirewallRule -DisplayName 'Cuttle*LAN*' -ErrorAction SilentlyContinue |
    Select-Object DisplayName, Enabled, Profile, Direction, Action |
    Format-Table -AutoSize

$public = @(Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' })
if ($public.Count -gt 0) {
    Write-Host 'Note: these networks are PUBLIC, so the rules above do not apply on them:' -ForegroundColor Yellow
    foreach ($p in $public) { Write-Host "  $($p.InterfaceAlias) ($($p.Name))" -ForegroundColor Yellow }
    Write-Host '  If this is your home network, set it to Private in Windows Settings:' -ForegroundColor Yellow
    Write-Host '  Settings > Network & Internet > (your network) > Private network' -ForegroundColor Yellow
}

Write-Host ''
if ($ok) {
    Write-Host 'Done. Restart Cuttle, then on your phone open:' -ForegroundColor Green
    $ip = $null
    try {
        $ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
            $_.IPAddress -match '^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)' } | Select-Object -First 1).IPAddress
    } catch {}
    if (-not $ip) { $ip = 'YOUR_PC_IP' }
    Write-Host "  https://${ip}:${PhonePort}/phone" -ForegroundColor White
    Write-Host "  http://${ip}:${HttpPort}/phone  (plain HTTP fallback)" -ForegroundColor Gray
} else {
    Write-Host 'Some rules failed - read errors above.' -ForegroundColor Red
}

Write-Host ''
Read-Host 'Press Enter to close'
