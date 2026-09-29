$ErrorActionPreference = "Continue"
$log = Join-Path $PSScriptRoot "setup-tailscale-ssh.log"
function Log($m) { $line = "[$(Get-Date -Format o)] $m"; Add-Content -Path $log -Value $line; Write-Host $line }

Remove-Item $log -ErrorAction SilentlyContinue
Log "=== Elevated Tailscale SSH setup starting ==="
Log "User: $env:USERNAME / IsAdmin: $(([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))"

# 1) Install OpenSSH Server if missing
$sshd = Get-Service sshd -ErrorAction SilentlyContinue
$cap = Get-WindowsCapability -Online | Where-Object Name -like "OpenSSH.Server*"
Log "Capability: $($cap | ForEach-Object { \"$($_.Name)=$($_.State)\" })"
if (-not $sshd -or -not (Test-Path "C:\Windows\System32\OpenSSH\sshd.exe")) {
  Log "Installing OpenSSH.Server~~~~0.0.1.0 ..."
  $install = Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
  Log "Install result: RestartNeeded=$($install.RestartNeeded) Path=$($install.Path)"
  $cap2 = Get-WindowsCapability -Online | Where-Object Name -like "OpenSSH.Server*"
  Log "Capability after install: $($cap2 | ForEach-Object { \"$($_.Name)=$($_.State)\" })"
} else {
  Log "OpenSSH Server already present"
}

# 2) Start + auto
Log "Configuring sshd service..."
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
$sshdAfter = Get-Service sshd
Log "sshd Status=$($sshdAfter.Status) StartType=$($sshdAfter.StartType)"

# Also ensure ssh-agent optional left alone unless needed - do not change per user request

# 3) Inspect existing firewall rules BEFORE changes
Log "=== Existing SSH/OpenSSH firewall rules (before) ==="
$existing = Get-NetFirewallRule | Where-Object { $_.DisplayName -match "SSH|OpenSSH" -or $_.Name -match "SSH|OpenSSH" }
foreach ($r in $existing) {
  $pf = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $r
  $af = Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $r
  $if = Get-NetFirewallInterfaceFilter -AssociatedNetFirewallRule $r
  Log ("BEFORE Name={0} DisplayName={1} Enabled={2} Direction={3} Action={4} Profile={5} Protocol={6} LocalPort={7} RemoteAddress={8} InterfaceAlias={9}" -f `
    $r.Name, $r.DisplayName, $r.Enabled, $r.Direction, $r.Action, $r.Profile, $pf.Protocol, ($pf.LocalPort -join ","), ($af.RemoteAddress -join ","), ($if.InterfaceAlias -join ","))
}

# Identify broad rules (port 22 inbound allow without Tailscale scoping)
$broad = @()
foreach ($r in $existing) {
  if ($r.Direction -ne "Inbound" -or $r.Action -ne "Allow") { continue }
  $pf = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $r
  $af = Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $r
  $if = Get-NetFirewallInterfaceFilter -AssociatedNetFirewallRule $r
  $ports = @($pf.LocalPort)
  $is22 = ($ports -contains "22") -or ($ports -contains "Any")
  if (-not $is22 -and $pf.Protocol -ne "Any") {
    # still might be OpenSSH named rule for 22
    if ($r.DisplayName -notmatch "OpenSSH|SSH") { continue }
  }
  $remoteAny = (-not $af.RemoteAddress) -or ($af.RemoteAddress -contains "Any")
  $ifaceAny = (-not $if.InterfaceAlias) -or ($if.InterfaceAlias -contains "Any")
  $looksBroad = $remoteAny -and $ifaceAny -and (($ports -contains "22") -or ($r.DisplayName -match "OpenSSH.*Server.*Inbound|OpenSSH SSH Server"))
  if ($looksBroad -or ($r.DisplayName -match "OpenSSH SSH Server \(sshd\)" -and $remoteAny -and $ifaceAny)) {
    $broad += $r
    Log "BROAD_RULE_DETECTED: $($r.Name) / $($r.DisplayName) Enabled=$($r.Enabled)"
  }
}

# 4) Create Tailscale-scoped rule (idempotent)
$ruleName = "OpenSSH-Server-Tailscale-Only"
$existingTs = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
$tailscaleIface = (Get-NetAdapter | Where-Object { $_.InterfaceDescription -match "Tailscale" -or $_.Name -eq "Tailscale" } | Select-Object -First 1).Name
Log "Tailscale interface alias: $tailscaleIface"

if ($existingTs) {
  Log "Updating existing Tailscale-only rule..."
  Set-NetFirewallRule -Name $ruleName -Enabled True -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 -RemoteAddress "100.64.0.0/10" -Profile Any
  if ($tailscaleIface) {
    Set-NetFirewallRule -Name $ruleName -InterfaceAlias $tailscaleIface
  }
} else {
  Log "Creating Tailscale-only firewall rule..."
  $params = @{
    Name = $ruleName
    DisplayName = "OpenSSH Server (sshd) Tailscale only"
    Description = "Allow inbound SSH (TCP/22) only from Tailscale CGNAT 100.64.0.0/10 via Tailscale interface"
    Enabled = $true
    Direction = "Inbound"
    Action = "Allow"
    Protocol = "TCP"
    LocalPort = 22
    RemoteAddress = "100.64.0.0/10"
    Profile = "Any"
  }
  if ($tailscaleIface) {
    $params.InterfaceAlias = $tailscaleIface
  }
  New-NetFirewallRule @params | Out-Null
}

# Disable broad rules if present (do not delete)
foreach ($r in $broad) {
  if ($r.Name -eq $ruleName) { continue }
  if ($r.Enabled) {
    Log "DISABLING broad rule (not deleting): $($r.Name) / $($r.DisplayName)"
    Disable-NetFirewallRule -Name $r.Name
  } else {
    Log "Broad rule already disabled: $($r.Name)"
  }
}

# 5) Verify
Log "=== Verification ==="
$svc = Get-Service sshd | Select-Object Name, Status, StartType, DisplayName
Log ("Get-Service sshd: Status={0} StartType={1}" -f $svc.Status, $svc.StartType)

$listen = Get-NetTCPConnection -LocalPort 22 -State Listen -ErrorAction SilentlyContinue
if ($listen) {
  foreach ($l in $listen) {
    Log ("LISTEN LocalAddress={0} LocalPort={1} State={2}" -f $l.LocalAddress, $l.LocalPort, $l.State)
  }
} else {
  # fallback netstat
  $ns = netstat -an | Select-String ":22\s"
  Log "LISTEN via netstat: $ns"
}

Log "=== Firewall rules after ==="
$after = Get-NetFirewallRule | Where-Object { $_.DisplayName -match "SSH|OpenSSH" -or $_.Name -match "SSH|OpenSSH" }
foreach ($r in $after) {
  $pf = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $r
  $af = Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $r
  $if = Get-NetFirewallInterfaceFilter -AssociatedNetFirewallRule $r
  Log ("AFTER Name={0} DisplayName={1} Enabled={2} Direction={3} Action={4} Profile={5} Protocol={6} LocalPort={7} RemoteAddress={8} InterfaceAlias={9}" -f `
    $r.Name, $r.DisplayName, $r.Enabled, $r.Direction, $r.Action, $r.Profile, $pf.Protocol, ($pf.LocalPort -join ","), ($af.RemoteAddress -join ","), ($if.InterfaceAlias -join ","))
}

$tsIp = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -eq "Tailscale" }
Log ("Tailscale IP: {0}/{1} on {2}" -f $tsIp.IPAddress, $tsIp.PrefixLength, $tsIp.InterfaceAlias)
Log "Windows username for SSH: $env:USERNAME"
Log "=== DONE ==="
