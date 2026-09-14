$ErrorActionPreference = "Stop"

$releaseDir = Join-Path $PSScriptRoot "..\release"
$sourceDir = Join-Path $releaseDir "win-unpacked"
$portableDir = Join-Path $releaseDir "VsCodeEn-portable"
$appDir = Join-Path $portableDir "VsCodeEn"
$zipPath = Join-Path $releaseDir "VsCodeEn-portable.zip"

if (!(Test-Path $sourceDir)) {
  throw "win-unpacked not found: $sourceDir"
}

# Stop running portable app instances to avoid file locks.
$running = Get-Process VsCodeEn -ErrorAction SilentlyContinue
if ($running) {
  foreach ($p in $running) {
    try {
      if ($p.Path -like "$portableDir*") {
        Stop-Process -Id $p.Id -Force -ErrorAction Stop
      }
    } catch {
      # ignore process inspection edge cases
    }
  }
  Start-Sleep -Milliseconds 500
}

if (Test-Path $portableDir) {
  Remove-Item $portableDir -Recurse -Force
}
New-Item -ItemType Directory -Path $appDir | Out-Null

Copy-Item -Path (Join-Path $sourceDir "*") -Destination $appDir -Recurse -Force

# Include local Gomoku and Go engine bundles for the authorized assistants.
$repoRapfiDir = Join-Path $PSScriptRoot "..\engines\rapfi"
$portableRapfiDir = Join-Path $appDir "engines\rapfi"
if (Test-Path $repoRapfiDir) {
  New-Item -ItemType Directory -Path $portableRapfiDir -Force | Out-Null
  Copy-Item -Path (Join-Path $repoRapfiDir "*") -Destination $portableRapfiDir -Recurse -Force
}

$repoKataGoDir = Join-Path $PSScriptRoot "..\engines\katago"
$portableKataGoDir = Join-Path $appDir "engines\katago"
if (Test-Path $repoKataGoDir) {
  New-Item -ItemType Directory -Path $portableKataGoDir -Force | Out-Null
  Copy-Item -Path (Join-Path $repoKataGoDir "*") -Destination $portableKataGoDir -Recurse -Force
}

# Include the local Pikafish bundle for the authorized Xiangqi assistant.
$repoPikafishDir = Join-Path $PSScriptRoot "..\engines\Pikafish"
$portablePikafishDir = Join-Path $appDir "engines\Pikafish"
if (Test-Path $repoPikafishDir) {
  New-Item -ItemType Directory -Path $portablePikafishDir -Force | Out-Null
  Copy-Item -Path (Join-Path $repoPikafishDir "*") -Destination $portablePikafishDir -Recurse -Force
}

$psLauncher = @'
$ErrorActionPreference = "Stop"

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $script:LogFile -Value $line -Encoding UTF8
}

Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue

$script:RootDir = $PSScriptRoot
$script:LogFile = Join-Path $RootDir "Start-VsCodeEn.log"
$exe = Join-Path $RootDir "VsCodeEn\VsCodeEn.exe"
$runtimeRoot = Join-Path $RootDir ".runtime"
$userDataDir = Join-Path $runtimeRoot "user-data"
$cacheDir = Join-Path $runtimeRoot "cache"

Write-Log "Launcher start"
Write-Log "Root: $RootDir"

if (-not (Test-Path $exe)) {
    Write-Log "ERROR: VsCodeEn.exe not found: $exe"
    throw "VsCodeEn.exe not found at: $exe"
}

New-Item -ItemType Directory -Force -Path $userDataDir | Out-Null
New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null
Write-Log "Runtime dirs ready"

$baseArgs = @(
    "--user-data-dir=$userDataDir",
    "--disk-cache-dir=$cacheDir",
    "--disable-gpu-shader-disk-cache",
    "--disable-features=NetworkChangeNotifier",
    "--disable-gpu"
)

function Try-Start {
    param([string[]]$LaunchArgs, [string]$Tag)
    Write-Log "Start attempt: $Tag"
    $p = Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe) -ArgumentList $LaunchArgs -PassThru
    Start-Sleep -Milliseconds 1800
    if ($p.HasExited) {
        Write-Log "Attempt failed ($Tag). ExitCode=$($p.ExitCode)"
        return $false
    }
    Write-Log "Attempt success ($Tag). PID=$($p.Id)"
    return $true
}

$ok = Try-Start -LaunchArgs $baseArgs -Tag "normal"

if (-not $ok) {
    Write-Log "ERROR: all start attempts failed"
    throw "VsCodeEn failed to start. Check Start-VsCodeEn.log"
}
'@
Set-Content -Path (Join-Path $portableDir "Start-VsCodeEn.ps1") -Value $psLauncher -Encoding UTF8

$cmdLauncher = @'
@echo off
setlocal
set ELECTRON_RUN_AS_NODE=
set SCRIPT_DIR=%~dp0

powershell -ExecutionPolicy Bypass -File "%SCRIPT_DIR%Start-VsCodeEn.ps1"
if %errorlevel% neq 0 (
  echo.
  echo VsCodeEn start failed. See log:
  echo %SCRIPT_DIR%Start-VsCodeEn.log
  echo.
  pause
  exit /b %errorlevel%
)
'@
Set-Content -Path (Join-Path $portableDir "Start-VsCodeEn.cmd") -Value $cmdLauncher -Encoding ASCII

if (Test-Path $zipPath) {
  Remove-Item $zipPath -Force
}
Compress-Archive -Path (Join-Path $portableDir "*") -DestinationPath $zipPath -Force

Write-Output "Portable package created:"
Write-Output $portableDir
Write-Output $zipPath

