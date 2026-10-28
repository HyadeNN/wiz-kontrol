# Builds a standalone "WiZ Kontrol.exe" into dist/.
# Usage:  .\scripts\build.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

python -m pip install --quiet -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "WiZ Kontrol" `
    --icon assets/icon.ico `
    --collect-data customtkinter `
    --hidden-import pystray._win32 `
    launcher.pyw

Write-Host "Built: dist\WiZ Kontrol.exe"
