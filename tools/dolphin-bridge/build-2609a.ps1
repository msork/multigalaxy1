[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SourceRoot,

    [Parameter(Mandatory = $true)]
    [string]$BuildDirectory,

    [int]$Jobs = 6
)

$ErrorActionPreference = 'Stop'

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)] [string]$FilePath,
        [Parameter(Mandatory = $true)] [string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$([IO.Path]::GetFileName($FilePath)) failed with exit code $LASTEXITCODE"
    }
}

$source = (Resolve-Path -LiteralPath $SourceRoot).Path
$build = [IO.Path]::GetFullPath($BuildDirectory)
$tag = (& git -C $source describe --tags --exact-match HEAD 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or $tag -ne '2609a') {
    throw "Expected exact Dolphin tag 2609a; source reports '$tag'"
}

$bridgeSource = Join-Path $source 'Source\Core\Core\SharedMemoryBridge.cpp'
$bridgeCore = Join-Path $source 'Source\Core\Core\Core.cpp'
$coreCmake = Join-Path $source 'Source\Core\Core\CMakeLists.txt'
foreach ($path in @($bridgeSource, $bridgeCore, $coreCmake)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "The MultiGalaxy1 Dolphin bridge is not integrated: missing $path"
    }
}
if (-not [IO.File]::ReadAllText($bridgeCore).Contains('s_multi_galaxy_bridge.PublishFrame')) {
    throw 'The Dolphin 2609a Core frame callback does not contain the MultiGalaxy1 publisher hook'
}

$cmakeCommand = Get-Command cmake -ErrorAction Stop
$ninjaCommand = Get-Command ninja -ErrorAction Stop
if (-not (Get-Command cl -ErrorAction SilentlyContinue)) {
    throw 'MSVC cl.exe is not on PATH. Run this script from a Visual Studio 2026 x64 developer shell.'
}
if ($Jobs -lt 1) {
    throw 'Jobs must be at least 1'
}

$toolchain = Join-Path $source 'CMake\DolphinToolchain.cmake'
Invoke-Native $cmakeCommand.Source @(
    '-S', $source,
    '-B', $build,
    '-G', 'Ninja',
    "-DCMAKE_MAKE_PROGRAM=$($ninjaCommand.Source)",
    "-DCMAKE_TOOLCHAIN_FILE=$toolchain",
    '-DARCH=x86_64',
    '-DCMAKE_BUILD_TYPE=Release',
    '-DUSE_SYSTEM_LIBS=OFF',
    '-DENABLE_CLI_TOOL=ON'
)

$cache = Join-Path $build 'CMakeCache.txt'
if (-not (Test-Path -LiteralPath $cache -PathType Leaf) -or
    -not (Select-String -LiteralPath $cache -Pattern '^ENABLE_CLI_TOOL:BOOL=ON$' -Quiet)) {
    throw 'CMake cache does not confirm ENABLE_CLI_TOOL=ON'
}

Invoke-Native $cmakeCommand.Source @(
    '--build', $build,
    '--target', 'dolphin-emu', 'dolphin-tool',
    '--parallel', "$Jobs"
)

$binaries = Join-Path $build 'Binaries'
$dolphin = Join-Path $binaries 'Dolphin.exe'
$dolphinTool = Join-Path $binaries 'DolphinTool.exe'
foreach ($path in @($dolphin, $dolphinTool)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Build completed without required executable: $path"
    }
    if ((Get-Item -LiteralPath $path).Length -le 0) {
        throw "Required executable is empty: $path"
    }
}

Write-Output "Dolphin 2609a bridge build succeeded: $dolphin"
Write-Output "DolphinTool 2609a build succeeded: $dolphinTool"
