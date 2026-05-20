$releaseDir = Join-Path $PSScriptRoot "..\release"
$sourceDir = Join-Path $releaseDir "win-unpacked"
$portableDir = Join-Path $releaseDir "VsCodeEn-portable"
$appDir = Join-Path $portableDir "VsCodeEn"
$zipPath = Join-Path $releaseDir "VsCodeEn-portable.zip"

if (!(Test-Path $sourceDir)) {
  throw "win-unpacked not found: $sourceDir"
}

if (Test-Path $portableDir) {
  Remove-Item $portableDir -Recurse -Force
}
New-Item -ItemType Directory -Path $appDir | Out-Null

Copy-Item -Path (Join-Path $sourceDir "*") -Destination $appDir -Recurse -Force

$launcher = @'
@echo off
setlocal
set ELECTRON_RUN_AS_NODE=
start "" "%~dp0VsCodeEn\VsCodeEn.exe"
'@
Set-Content -Path (Join-Path $portableDir "Start-VsCodeEn.cmd") -Value $launcher -Encoding ASCII

if (Test-Path $zipPath) {
  Remove-Item $zipPath -Force
}
Compress-Archive -Path (Join-Path $portableDir "*") -DestinationPath $zipPath -Force

Write-Output "Portable package created:"
Write-Output $portableDir
Write-Output $zipPath
