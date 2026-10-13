# DZ23 Trading AI — Windows Offline/Local Installer Script (V6)
# Instala e configura o aplicativo DZ23 Trading AI no Windows 10/11 x64

$ErrorActionPreference = "Stop"

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " DZ23 Trading AI — Instalador do Proprietário (Windows) " -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

$InstallDir = "$env:LocalAppData\DZ23-Trading-AI"
$AppDataDir = "$env:AppData\DZ23-Trading-AI"
$DesktopDir = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
$StartMenuDir = "$env:AppData\Microsoft\Windows\Start Menu\Programs\DZ23 Trading AI"

Write-Host "Criando diretórios de instalação e dados..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path $AppDataDir | Out-Null
New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null

$ExecutablePath = "$InstallDir\DZ23-Trading-AI.exe"
$SourceExecutable = "$PSScriptRoot\..\..\dist\DZ23-Trading-AI\DZ23-Trading-AI.exe"

if (Test-Path $SourceExecutable) {
    Write-Host "Copiando pacote compilado do aplicativo..." -ForegroundColor Yellow
    Copy-Item -Path "$PSScriptRoot\..\..\dist\DZ23-Trading-AI\*" -Destination $InstallDir -Recurse -Force
} else {
    Write-Host "Copiando código-fonte para execução de desenvolvimento..." -ForegroundColor Yellow
    Copy-Item -Path "$PSScriptRoot\..\..\_compat.py", "$PSScriptRoot\..\..\pyproject.toml" -Destination $InstallDir -ErrorAction SilentlyContinue
    $ExecutablePath = "pythonw.exe"
}

Write-Host "Criando atalhos na Área de Trabalho e no Menu Iniciar..." -ForegroundColor Yellow
$WshShell = New-Object -ComObject WScript.Shell

# Atalho no Menu Iniciar
$StartShortcut = $WshShell.CreateShortcut("$StartMenuDir\DZ23 Trading AI.lnk")
if (Test-Path "$InstallDir\DZ23-Trading-AI.exe") {
    $StartShortcut.TargetPath = "$InstallDir\DZ23-Trading-AI.exe"
    $StartShortcut.WorkingDirectory = $InstallDir
} else {
    $StartShortcut.TargetPath = "pythonw.exe"
    $StartShortcut.Arguments = "-m desktop.launcher"
    $StartShortcut.WorkingDirectory = "$PSScriptRoot\..\.."
}
$StartShortcut.Description = "DZ23 Trading AI — Agência de Trading Particular"
$StartShortcut.Save()

# Atalho na Área de Trabalho
$DesktopShortcut = $WshShell.CreateShortcut("$DesktopDir\DZ23 Trading AI.lnk")
$DesktopShortcut.TargetPath = $StartShortcut.TargetPath
$DesktopShortcut.Arguments = $StartShortcut.Arguments
$DesktopShortcut.WorkingDirectory = $StartShortcut.WorkingDirectory
$DesktopShortcut.Description = "DZ23 Trading AI — Agência de Trading Particular"
$DesktopShortcut.Save()

Write-Host "`nInstalação concluída com sucesso!" -ForegroundColor Green
Write-Host "Você pode abrir o DZ23 Trading AI pelos atalhos criados na Área de Trabalho e no Menu Iniciar." -ForegroundColor Green
