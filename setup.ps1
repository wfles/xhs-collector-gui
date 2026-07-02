$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir = Join-Path $RootDir "XHS-Downloader"

git -C $RootDir submodule update --init --recursive

Set-Location $AppDir
uv sync --no-dev

Write-Host "Setup complete. Run .\start-xhs-gui.ps1 from the project root."
