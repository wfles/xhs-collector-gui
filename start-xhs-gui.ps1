$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RootDir

if (Get-Command python -ErrorAction SilentlyContinue) {
    python xhs_gui.py @args
}
elseif (Get-Command py -ErrorAction SilentlyContinue) {
    py -3 xhs_gui.py @args
}
else {
    throw "Python was not found. Install Python 3.12+ and try again."
}
