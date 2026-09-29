# Install Cuttle LAN-only mesh SSH pubkey into Windows OpenSSH authorized keys.
# Run elevated on the TARGET machine.
# Restricts this key to source IPs on your LAN CIDR + loopback.

param(
    [string]$PubKeyFile = "",
    [string]$LanCidr = "192.168.0.0/16"
)

$ErrorActionPreference = "Stop"
if (-not $PubKeyFile) {
    $candidates = @(
        Join-Path $PSScriptRoot "..\keys\cuttle_mesh_lan.pub",
        (Join-Path $env:USERPROFILE ".ssh\cuttle_mesh_lan.pub")
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $PubKeyFile = $c; break }
    }
}
if (-not $PubKeyFile -or -not (Test-Path $PubKeyFile)) {
    throw "Pubkey file not found. Pass -PubKeyFile path\to\cuttle_mesh_lan.pub"
}

$pub = (Get-Content $PubKeyFile -Raw).Trim()
if ($pub -notmatch '^\s*ssh-') { throw "Does not look like an OpenSSH public key: $PubKeyFile" }
$line = "from=`"$LanCidr,127.0.0.1,::1`" $pub"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).
    IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw "Run elevated (Administrators). Windows OpenSSH uses ProgramData keys for admin users."
}

$ak = Join-Path $env:ProgramData "ssh\administrators_authorized_keys"
$existing = @()
if (Test-Path $ak) {
    $existing = Get-Content $ak | Where-Object { $_ -and ($_ -notmatch "cuttle-mesh-lan-only") }
}
Set-Content -Path $ak -Value (($existing + $line) -join "`n") -Encoding ascii -Force
icacls $ak /inheritance:r | Out-Null
icacls $ak /grant "SYSTEM:(F)" | Out-Null
icacls $ak /grant "BUILTIN\Administrators:(F)" | Out-Null
Write-Host "Installed LAN-restricted cuttle_mesh_lan key into $ak"
Write-Host "from=$LanCidr (+ loopback)"
