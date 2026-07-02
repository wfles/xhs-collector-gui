$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir = Join-Path $RootDir "XHS-Downloader"
$OutputRoot = Join-Path $HOME "Downloads"
$OutputFolder = "XHS-Downloads"
$UrlText = ($args -join " ")

if ([string]::IsNullOrWhiteSpace($UrlText)) {
    Write-Host "Usage: .\download-xhs.ps1 '<xiaohongshu share link or note link>'"
    exit 2
}

New-Item -ItemType Directory -Force -Path (Join-Path $OutputRoot $OutputFolder) | Out-Null

Set-Location $AppDir
uv run --no-dev main.py `
    --url $UrlText `
    --work_path $OutputRoot `
    --folder_name $OutputFolder `
    --record_data false `
    --image_format AUTO `
    --folder_mode false `
    --author_archive false `
    --download_record false `
    --live_download true `
    --write_mtime true `
    --language zh_CN
