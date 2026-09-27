# Bump Cuttle desktop/mesh version (electron/package.json).
# Workers advertise this as cuttle_version — bump when Client/worker runtime
# behavior changes so host can see mismatches and offer self-update.
#
# Usage:
#   .cuttle\scripts\bump-cuttle-version.ps1           # patch 0.2.5 -> 0.2.6
#   .cuttle\scripts\bump-cuttle-version.ps1 -Minor    # 0.2.5 -> 0.3.0
#   .cuttle\scripts\bump-cuttle-version.ps1 -Major    # 0.2.5 -> 1.0.0
#   .cuttle\scripts\bump-cuttle-version.ps1 -Set 0.2.7

param(
    [switch]$Major,
    [switch]$Minor,
    [string]$Set = ""
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$pkgPath = Join-Path $root "electron\package.json"
if (-not (Test-Path $pkgPath)) {
    Write-Error "missing $pkgPath"
    exit 1
}

$raw = Get-Content -Raw -Path $pkgPath -Encoding UTF8
$pkg = $raw | ConvertFrom-Json
$old = [string]$pkg.version
if (-not $old) { Write-Error "no version in package.json"; exit 1 }

if ($Set) {
    $new = $Set.Trim()
} else {
    $parts = $old.Split(".")
    while ($parts.Count -lt 3) { $parts += "0" }
    $maj = [int]$parts[0]; $min = [int]$parts[1]; $pat = [int]$parts[2]
    if ($Major) {
        $maj++; $min = 0; $pat = 0
    } elseif ($Minor) {
        $min++; $pat = 0
    } else {
        $pat++
    }
    $new = "$maj.$min.$pat"
}

if ($new -eq $old) {
    Write-Output "{`"success`":true,`"unchanged`":true,`"version`":`"$old`"}"
    exit 0
}

# Preserve formatting: replace version field only
$updated = [regex]::Replace(
    $raw,
    '("version"\s*:\s*")([^"]+)(")',
    { param($m) $m.Groups[1].Value + $new + $m.Groups[3].Value },
    1
)
[System.IO.File]::WriteAllText($pkgPath, $updated, [System.Text.UTF8Encoding]::new($false))

Write-Output "{`"success`":true,`"old`":`"$old`",`"version`":`"$new`",`"path`":`"electron/package.json`"}"
