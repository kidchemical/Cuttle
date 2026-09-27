# Schedule a Flask-only restart via the durable API (daemon-owned).
# Do NOT taskkill web_chat_api from this process — that destroys the chat
# that requested the restart. Flask persists an ack, then the daemon replaces it.
$ErrorActionPreference = 'Stop'
$api = 'https://127.0.0.1:8080/api/flask/restart'
# Mode comes from the action params (CUTTLE_PARAM_MODE); CUTTLE_RESTART_MODE
# stays supported for direct shell invocations.
$mode = @($env:CUTTLE_PARAM_MODE, $env:CUTTLE_RESTART_MODE, 'graceful') |
    Where-Object { $_ } | Select-Object -First 1
$mode = $mode.Trim().ToLower()
$valid = @('graceful', 'when-idle', 'force', 'status')
if ($valid -notcontains $mode) {
    Write-Output ("Unknown restart mode '" + $mode + "'. Use: " + ($valid -join ', '))
    exit 1
}
$sessionId = @($env:CUTTLE_PARAM_SESSION_ID, $env:CUTTLE_SESSION_ID, $env:CUTTLE_RESTART_SESSION_ID) |
    Where-Object { $_ } | Select-Object -First 1
# Card-driven restarts report progress on the action card itself, so Flask must
# not persist ack/completion bubbles into the transcript. CUTTLE_PARAM_CHAT_NOTIFY
# (or CUTTLE_RESTART_CHAT_NOTIFY) opts back in for plain shell callers.
$notifyRaw = @($env:CUTTLE_PARAM_CHAT_NOTIFY, $env:CUTTLE_RESTART_CHAT_NOTIFY) |
    Where-Object { $_ } | Select-Object -First 1
$chatNotify = $false
if ($notifyRaw) {
    $chatNotify = @('1', 'true', 'yes', 'on') -contains $notifyRaw.Trim().ToLower()
}
$bodyObj = @{
    mode        = $mode
    source      = 'flask.restart_action'
    chat_notify = $chatNotify
}
if ($sessionId) {
    $bodyObj.session_id = $sessionId
}
if ($mode -eq 'force') {
    $bodyObj.confirm = $true
}
$body = $bodyObj | ConvertTo-Json -Compress

try {
    Add-Type @"
using System.Net;
using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
public class CuttleTlsRestartReq {
  public static void Trust() {
    ServicePointManager.ServerCertificateValidationCallback =
      delegate { return true; };
  }
}
"@
    [CuttleTlsRestartReq]::Trust()
} catch {}

# The card shows this text as its status line, so keep it to one short phrase.
function Format-RestartResult([string]$json) {
    if (-not $json) { return '' }
    try { $o = $json | ConvertFrom-Json } catch { return $json }
    $busy = ''
    if ($o.active_work) {
        $busy = if ($o.active_work.is_idle) { 'no active work' }
                else { "" + $o.active_work.active_count + " active task(s)" }
    }
    if ($o.error) { return [string]$o.error }
    $state = if ($o.state) { [string]$o.state } elseif ($o.status) { [string]$o.status.state } else { '' }
    switch ($state) {
        'waiting_for_idle' { return "Waiting for $busy to finish…" }
        'acknowledged'     { return 'Restarting Flask…' }
        'preparing'        { return 'Restarting Flask…' }
        'rejected'         { return "Postponed — $busy still running" }
        'healthy'          { return "Last restart healthy ($busy)" }
        default {
            if ($state) { return "$state ($busy)" }
            if ($o.response) { return ([string]$o.response -replace '\s+', ' ').Trim() }
            return $json
        }
    }
}

try {
    $r = Invoke-WebRequest -Uri $api -Method POST -Body $body -ContentType 'application/json' `
        -TimeoutSec 20 -UseBasicParsing
    Write-Output (Format-RestartResult $r.Content)
    exit 0
} catch {
    $detail = ''
    try {
        $stream = $_.Exception.Response.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($stream)
        $detail = Format-RestartResult $reader.ReadToEnd()
    } catch {}
    if (-not $detail) { $detail = $_.Exception.Message }
    Write-Output ("Restart (" + $mode + ") failed: " + $detail)
    exit 1
}
