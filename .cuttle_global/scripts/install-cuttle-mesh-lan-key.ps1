# Cuttle mesh SSH pairing (Windows) — per-install identities, explicit authorization.
#
# Cuttle never distributes a shared trusted key: every installation owns its
# keypair (see -Generate) and a target machine authorizes exactly the peer
# keys its operator installs (see -PubKeyFile). Existing authorized_keys
# entries — including legacy shared-key lines — are preserved, never purged
# or rotated by this script.
#
#   Per-install identity (unelevated, safe to re-run):
#     .\install-cuttle-mesh-lan-key.ps1 -Generate
#   Authorize a peer (run ELEVATED on the TARGET machine):
#     .\install-cuttle-mesh-lan-key.ps1 -PubKeyFile \\peer\share\peer.pub -Alias "peer-name"
#
# Installed keys are restricted to your LAN CIDR + loopback via from="...".

param(
    [string]$PubKeyFile = "",
    [string]$Alias = "",
    [string]$LanCidr = "192.168.0.0/16",
    [switch]$Generate,
    [string]$KeyFile = (Join-Path $env:USERPROFILE ".ssh\cuttle_mesh_lan")
)

$ErrorActionPreference = "Stop"

function Get-KeyBody([string]$line) {
    # The key blob (longest whitespace-separated field) for idempotent
    # compare — field positions vary with from="..."/comment decorations.
    $best = ""
    foreach ($p in ($line.Trim() -split '\s+')) {
        if ($p.Length -gt $best.Length) { $best = $p }
    }
    return $best
}

if ($Generate) {
    if (Test-Path $KeyFile) {
        Write-Host "Keypair already exists (never overwritten): $KeyFile"
    } else {
        if (-not (Get-Command ssh-keygen -ErrorAction SilentlyContinue)) {
            throw "ssh-keygen not found. Install OpenSSH client first."
        }
        $comment = "cuttle-mesh-$($env:COMPUTERNAME)"
        ssh-keygen -t ed25519 -f $KeyFile -N '' -C $comment | Out-Null
        Write-Host "Generated per-install keypair: $KeyFile"
    }
    $pubFile = "$KeyFile.pub"
    if (Test-Path $pubFile) {
        Write-Host "Public key (share THIS with pairing targets): $pubFile"
        Get-Content $pubFile
    }
    return
}

if (-not $PubKeyFile -or -not (Test-Path $PubKeyFile)) {
    throw ("Explicit pairing required: pass -PubKeyFile <peer's .pub> " +
        "(and -Alias <name>). This script never installs a default/shared key.")
}

$pub = (Get-Content $PubKeyFile -Raw).Trim()
if ($pub -notmatch '^\s*ssh-') { throw "Does not look like an OpenSSH public key: $PubKeyFile" }
$peerTag = if ($Alias) { $Alias } else { [System.IO.Path]::GetFileNameWithoutExtension($PubKeyFile) }
$line = "from=`"$LanCidr,127.0.0.1,::1`" $pub cuttle-mesh-peer=`"$peerTag`""

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).
    IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw "Run elevated (Administrators). Windows OpenSSH uses ProgramData keys for admin users."
}

$ak = Join-Path $env:ProgramData "ssh\administrators_authorized_keys"
$existing = @()
if (Test-Path $ak) {
    $existing = @(Get-Content $ak)
}
$newBody = Get-KeyBody $pub
foreach ($e in $existing) {
    if ($e -and (Get-KeyBody $e) -eq $newBody) {
        Write-Host "Peer key already authorized (idempotent, no change): $peerTag"
        return
    }
}
# Preserve every existing entry (including legacy shared-key lines — removal
# is a deliberate manual rotation, never a side effect of pairing).
Set-Content -Path $ak -Value (($existing + @($line) | Where-Object { $_ }) -join "`n") -Encoding ascii -Force
icacls $ak /inheritance:r | Out-Null
icacls $ak /grant "SYSTEM:(F)" | Out-Null
icacls $ak /grant "BUILTIN\Administrators:(F)" | Out-Null
Write-Host "Authorized LAN-restricted peer key in $ak"
Write-Host "peer=$peerTag from=$LanCidr (+ loopback)"
