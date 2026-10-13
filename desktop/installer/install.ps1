# DZ23 Trading AI — Windows Offline/Local Installer Script
# Instala e configura o aplicativo DZ23 Trading AI no Windows 10/11 x64

$ErrorActionPreference = "Stop"

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " DZ23 Trading AI — Instalador do Proprietário (Windows) " -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

$InstallDir = "$env:LocalAppData\DZ23-Trading-AI"
$AppDataDir = "$env:AppData\DZ23-Trading-AI"

Write-Host "Criando diretórios de instalação e dados..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path $AppDataDir | Out-Null

Write-Host "Copiando arquivos do sistema..." -ForegroundColor Yellow
Copy-Item -Path "$PSScriptRoot\..\..\_compat.py", "$PSScriptRoot\..\..\pyproject.toml" -Destination $InstallDir -ErrorAction SilentlyContinue

Write-Host "Criando atalho no Menu Iniciar..." -ForegroundColor Yellow
$WshShell = New-Object -ComObject WScript.Shell
$StartMenuDir = "$env:AppData\Microsoft\Windows\Start Menu\Programs\DZ23 Trading AI"
New-Item -ItemType Directory -Force -Path $StartMenuDir | Out-Null
$Shortcut = $WshShell.CreateShortcut("$StartMenuDir\DZ23 Trading AI.lnk")
$Shortcut.TargetPath = "python.exe"
$Shortcut.Arguments = "-m desktop.launcher"
$Shortcut.WorkingDirectory = $InstallDir
$Shortcut.Description = "DZ23 Trading AI — Agência de Trading Particular"
$Shortcut.Save()

Write-Host "`nInstalação concluída com sucesso!" -ForegroundColor Green
Write-Host "Você pode abrir o DZ23 Trading AI pelo Menu Iniciar ou executando 'python -m desktop.launcher'." -ForegroundColor Green
