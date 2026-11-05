param(
    [Parameter(Mandatory = $true)]
    [string] $SourceRoot
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $SourceRoot).Path
$tag = (& git -C $root describe --tags --exact-match HEAD 2>$null)
if ($LASTEXITCODE -ne 0 -or $tag.Trim() -ne '2609a') {
    throw "Expected a clean Dolphin source checkout exactly at tag 2609a; found '$tag'."
}
$status = @(& git -C $root status --porcelain)
if ($status.Count -ne 0) {
    throw 'Refusing to modify a Dolphin source tree with existing local changes.'
}

$coreDir = Join-Path $root 'Source\Core\Core'
$header = Join-Path $coreDir 'SharedMemoryBridge.h'
$implementation = Join-Path $coreDir 'SharedMemoryBridge.cpp'
if ((Test-Path -LiteralPath $header) -or (Test-Path -LiteralPath $implementation)) {
    throw 'Dolphin bridge files already exist; refusing to overwrite them.'
}

$patch = Join-Path $PSScriptRoot 'integration-2609a.patch'
& git -C $root apply --check $patch
if ($LASTEXITCODE -ne 0) {
    throw 'The integration patch did not match Dolphin tag 2609a; no source files were changed.'
}

try {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'SharedMemoryBridge.h') -Destination $header
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'SharedMemoryBridge.cpp') -Destination $implementation
    & git -C $root apply $patch
    if ($LASTEXITCODE -ne 0) {
        throw 'git apply failed.'
    }
} catch {
    Remove-Item -LiteralPath $header, $implementation -Force -ErrorAction SilentlyContinue
    throw
}

Write-Host "Applied MultiGalaxy1 bridge sources to Dolphin 2609a at $root."
Write-Host 'The modified Dolphin source remains GPL-2.0-or-later; see tools/dolphin-bridge/README.md.'
