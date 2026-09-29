$ErrorActionPreference = "Continue"
$log = Join-Path $PSScriptRoot "setup-tailscale-ssh-verify.log"
function Log($m) { $line = "[$(Get-Date -Format o)] $m"; Add-Content -Path $log -Value $line }

Remove-Item $log -ErrorAction SilentlyContinue
Log "=== Verify Tailscale SSH rule ==="

# List ALL rules matching our name or OpenSSH
$rules = Get-NetFirewallRule | Where-Object {
  $_.Name -like "*OpenSSH*" -or $_.Name -like "*Tailscale*" -or $_.DisplayName -like "*OpenSSH*" -or $_.DisplayName -like "*SSH*"
}
foreach ($r in $rules) {
  $pf = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $r
  $af = Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $r
  $if = Get-NetFirewallInterfaceFilter -AssociatedNetFirewallRule $r
  Log ("RULE Name={0} Display={1} Enabled={2} Dir={3} Action={4} Proto={5} LocalPort={6} Remote={7} Iface={8}" -f `
    $r.Name, $r.DisplayName, $r.Enabled, $r.Direction, $r.Action, $pf.Protocol, ($pf.LocalPort -join ","), ($af.RemoteAddress -join ","), ($if.InterfaceAlias -join ","))
}

$ts = Get-NetFirewallRule -Name "OpenSSH-Server-Tailscale-Only" -ErrorAction SilentlyContinue
if (-not $ts) {
  Log "MISSING Tailscale-only rule — recreating..."
  New-NetFirewallRule `
    -Name "OpenSSH-Server-Tailscale-Only" `
    -DisplayName "OpenSSH Server (sshd) Tailscale only" `
    -Description "Allow inbound SSH (TCP/22) only from Tailscale CGNAT 100.64.0.0/10 via Tailscale interface" `
    -Enabled True `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort 22 `
    -RemoteAddress "100.64.0.0/10" `
    -InterfaceAlias "Tailscale" `
    -Profile Any | Out-Null
  $ts = Get-NetFirewallRule -Name "OpenSSH-Server-Tailscale-Only" -ErrorAction SilentlyContinue
  Log "Recreate result: exists=$([bool]$ts) Enabled=$($ts.Enabled)"
} else {
  Log "Tailscale-only rule exists Enabled=$($ts.Enabled)"
}

# Confirm filters on our rule
$r = Get-NetFirewallRule -Name "OpenSSH-Server-Tailscale-Only"
$pf = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $r
$af = Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $r
$if = Get-NetFirewallInterfaceFilter -AssociatedNetFirewallRule $r
Log ("FINAL Name={0} Enabled={1} Proto={2} LocalPort={3} Remote={4} Iface={5}" -f $r.Name, $r.Enabled, $pf.Protocol, ($pf.LocalPort -join ","), ($af.RemoteAddress -join ","), ($if.InterfaceAlias -join ","))

Get-Service sshd | ForEach-Object { Log ("sshd Status={0} StartType={1}" -f $_.Status, $_.StartType) }
Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
  Log ("LISTEN {0}:{1}" -f $_.LocalAddress, $_.LocalPort)
}

# Local connectivity smoke test to 100.111.87.79:22
try {
  $tcp = New-Object System.Net.Sockets.TcpClient
  $iar = $tcp.BeginConnect("100.111.87.79", 22, $null, $null)
  $ok = $iar.AsyncWaitHandle.WaitOne(3000, $false)
  if ($ok -and $tcp.Connected) { Log "TCP connect to 100.111.87.79:22 SUCCEEDED" }
  else { Log "TCP connect to 100.111.87.79:22 FAILED/timeout" }
  $tcp.Close()
} catch {
  Log "TCP connect exception: $($_.Exception.Message)"
}

Log "=== DONE ==="
