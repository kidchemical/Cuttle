# Standalone listener — tests whether ANY phone traffic reaches this PC (not Cuttle).
# Run as Administrator. On phone open: http://PC_IP:9999/
$port = 9999
$ErrorActionPreference = 'Stop'

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host 'Run as Administrator (needs urlacl + firewall).' -ForegroundColor Red
    Read-Host 'Press Enter'
    exit 1
}

$urlacl = "http://+:${port}/"
netsh http add urlacl url=$urlacl user=Everyone 2>$null | Out-Null
New-NetFirewallRule -DisplayName "Cuttle LAN isolation test" -Direction Inbound -Protocol TCP -LocalPort $port -Action Allow -Profile Private,Public -RemoteAddress Any -ErrorAction SilentlyContinue | Out-Null

$ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -match 'Wi-Fi|WLAN' } | Select-Object -First 1).IPAddress
Write-Host ''
Write-Host "=== Phone isolation test ===" -ForegroundColor Cyan
Write-Host "On your phone (Chrome, mobile data OFF), open:" -ForegroundColor White
Write-Host "  http://${ip}:${port}/" -ForegroundColor Yellow
Write-Host ''
Write-Host 'If you see "OK from PC" on the phone, the network path works and Cuttle config is the issue.'
Write-Host 'If the phone times out, your router blocks phone-to-PC (AP isolation) or phone is on a different subnet.'
Write-Host 'Press Ctrl+C to stop.'
Write-Host ''

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add($urlacl)
$listener.Start()
while ($listener.IsListening) {
    $ctx = $listener.GetContext()
    $remote = $ctx.Request.RemoteEndPoint.Address.ToString()
    $ua = $ctx.Request.UserAgent
    $msg = "<html><body><h1>OK from PC</h1><p>Your phone IP: $remote</p><p>Network path works.</p></body></html>"
    $buf = [System.Text.Encoding]::UTF8.GetBytes($msg)
    $ctx.Response.ContentType = 'text/html'
    $ctx.Response.ContentLength64 = $buf.Length
    $ctx.Response.OutputStream.Write($buf, 0, $buf.Length)
    $ctx.Response.Close()
    Write-Host "[$(Get-Date -Format HH:mm:ss)] HIT from $remote — $ua" -ForegroundColor Green
}
