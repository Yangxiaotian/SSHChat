Param(
  [string]$BundleFile = ""
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

if (-not $BundleFile) {
  if ($env:SSHCHAT_BUNDLE_FILE) {
    $BundleFile = $env:SSHCHAT_BUNDLE_FILE
  } else {
    $BundleFile = Join-Path $root "dist\client-bundle.json"
  }
}

if (-not (Test-Path $BundleFile)) {
  Write-Error "missing bundle file: $BundleFile"
}

$packVenv = Join-Path $root "build\pack-venv"
$workPath = Join-Path $root "build\pyinstaller"
$distPath = Join-Path $root "dist-packages"
$pyiCache = Join-Path $root "build\pyinstaller-cache"

foreach ($p in @($packVenv, $workPath, $distPath)) {
  if (Test-Path $p) {
    try {
      Remove-Item -Recurse -Force $p
    } catch {
      Write-Error "cannot remove $p. Avoid running as Administrator. If previously created by admin, fix ownership/ACL then retry."
    }
  }
}

New-Item -ItemType Directory -Force $pyiCache | Out-Null
$env:PYINSTALLER_CONFIG_DIR = $pyiCache

python -m venv $packVenv
$activate = Join-Path $packVenv "Scripts\Activate.ps1"
if (-not (Test-Path $activate)) {
  Write-Error "venv activation script not found: $activate"
}
. $activate

pip install -q -r (Join-Path $root "requirements-gui.txt") -r (Join-Path $root "requirements-packaging.txt")

$iconArg = @()
$iconPath = Join-Path $root "electron\assets\icon.ico"
if (Test-Path $iconPath) {
  $iconArg = @("--icon", $iconPath)
} else {
  Write-Warning "app icon not found at $iconPath; using PyInstaller default"
}

python -m PyInstaller `
  --clean `
  --noconfirm `
  --noconsole `
  --name SSHChat `
  @iconArg `
  --paths $root `
  --hidden-import sshchat_client_util `
  --hidden-import PIL `
  --hidden-import PIL.Image `
  --collect-all paramiko `
  --collect-all cryptography `
  --collect-all PIL `
  --distpath $distPath `
  --workpath $workPath `
  --add-data "$BundleFile;." `
  (Join-Path $root "sshchat_gui.py")

Write-Host ""
Write-Host "Built under: $distPath"
Write-Host "  Windows: $distPath\SSHChat.exe"
