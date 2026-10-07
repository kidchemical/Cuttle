# Standalone listener: tests whether a phone on this PC's subnet can reach it at
# all (AP isolation, wrong subnet), independent of Cuttle. Run as Administrator.
# On the phone open: http://PC_IP:9999/
#
# Scope: the temporary rule allows LocalSubnet on Private networks only, the URL
# reservation is for the current user, and both are removed when the test
# ends (Ctrl+C or after 5 minutes).
$port = 9999
$minutes = 5
$ruleName = 'Cuttle LAN isolation test'
$ErrorActionPreference = 'Stop'

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host 'Run as Administrator (needs a URL reservation + firewall rule).' -ForegroundColor Red
    Read-Host 'Press Enter'
    exit 1
}

$prefix = "http://+:${port}/"
$user = "$env:USERDOMAIN\$env:USERNAME"
$listener = $null
try {
    netsh http add urlacl url=$prefix user=$user 2>$null | Out-Null
    Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $port `
        -Action Allow -Profile Private -RemoteAddress LocalSubnet | Out-Null

    $ip = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
        $_.IPAddress -match '^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)' } | Select-Object -First 1).IPAddress
    Write-Host ''
    Write-Host '=== Phone isolation test ===' -ForegroundColor Cyan
    Write-Host 'On your phone (Wi-Fi on, mobile data OFF), open:' -ForegroundColor White
    Write-Host "  http://${ip}:${port}/" -ForegroundColor Yellow
    Write-Host ''
    Write-Host 'If the phone shows "OK from PC", the network path works and Cuttle config is the issue.'
    Write-Host 'If it times out, the router blocks phone-to-PC (AP isolation), the phone is on another'
    Write-Host 'subnet, or this network is set to Public in Windows (the test rule is Private-only).'
    Write-Host "Stops after $minutes minutes, or press Ctrl+C. The rule and reservation are removed on exit."
    Write-Host ''

    $listener = New-Object System.Net.HttpListener
    $listener.Prefixes.Add($prefix)
    $listener.Start()
    $deadline = (Get-Date).AddMinutes($minutes)
    while ($listener.IsListening -and (Get-Date) -lt $deadline) {
        # Poll so Ctrl+C reaches the finally block (GetContext() blocks uninterruptibly).
        $pending = $listener.BeginGetContext($null, $null)
        while (-not $pending.AsyncWaitHandle.WaitOne(500)) {
            if ((Get-Date) -ge $deadline) { break }
        }
        if (-not $pending.IsCompleted) { break }
        $ctx = $listener.EndGetContext($pending)
        $remote = $ctx.Request.RemoteEndPoint.Address.ToString()
        $msg = "<html><body><h1>OK from PC</h1><p>Your phone IP: $remote</p><p>Network path works.</p></body></html>"
        $buf = [System.Text.Encoding]::UTF8.GetBytes($msg)
        $ctx.Response.ContentType = 'text/html; charset=utf-8'
        $ctx.Response.ContentLength64 = $buf.Length
        $ctx.Response.OutputStream.Write($buf, 0, $buf.Length)
        $ctx.Response.Close()
        Write-Host "[$(Get-Date -Format HH:mm:ss)] HIT from $remote" -ForegroundColor Green
    }
} finally {
    if ($listener) { try { $listener.Close() } catch {} }
    Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    netsh http delete urlacl url=$prefix 2>$null | Out-Null
    Write-Host 'Test ended: firewall rule and URL reservation removed.' -ForegroundColor Cyan
}
