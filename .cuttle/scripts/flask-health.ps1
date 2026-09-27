# GET /api/health with self-signed TLS ignored.
$ErrorActionPreference = 'Stop'
try {
    add-type @"
using System.Net;
using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
public class CuttleTlsHealth { public static void Trust() {
  ServicePointManager.ServerCertificateValidationCallback =
    delegate { return true; };
}}
"@
    [CuttleTlsHealth]::Trust()
} catch {}
$r = Invoke-WebRequest 'https://127.0.0.1:8080/api/health' -TimeoutSec 8 -UseBasicParsing
$snippet = $r.Content
if ($snippet.Length -gt 200) { $snippet = $snippet.Substring(0, 200) }
Write-Output ("HTTP " + [int]$r.StatusCode + " " + $snippet)
