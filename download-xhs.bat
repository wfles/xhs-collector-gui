@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0download-xhs.ps1" %*
