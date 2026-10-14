# DZ23 Trading AI - rollback: restaura um backup feito por backup_dados.ps1.
# Verifica o MANIFEST.sha256 ANTES de copiar (backup valido) e DEPOIS de
# restaurar (destino == manifest). Nao sobrescreve um destino existente sem
# -Forcar. Nunca toca em outra pasta alem do destino informado.
#
# Saida KEY=VALUE (ASCII): RESULTADO=OK|FALHOU, ARQUIVOS, MOTIVO.
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$Backup,
  [string]$Destino = "$env:APPDATA\DZ23-Trading-AI",
  [switch]$Forcar,
  [string]$SaidaArquivo = ''
)

$ErrorActionPreference = 'Stop'
$script:LINHAS = New-Object System.Collections.Generic.List[string]

function Add-Linha([string]$Texto) { $script:LINHAS.Add($Texto) | Out-Null }

function Write-Saida() {
  if ($SaidaArquivo -ne '') {
    [System.IO.File]::WriteAllLines($SaidaArquivo, $script:LINHAS,
      (New-Object System.Text.UTF8Encoding($false)))
  }
  foreach ($l in $script:LINHAS) { Write-Output $l }
}

function Encerrar([int]$Codigo) {
  Write-Saida
  exit $Codigo
}

# SHA256 via .NET puro: Get-FileHash vive em Microsoft.PowerShell.Utility e
# pode nao autocarregar no ambiente filho do instalador (evidencia CI c3:
# "Get-FileHash is not recognized"). O tipo abaixo e do runtime .NET e
# resolve sem modulo algum.
function Hash-Sha256([string]$Caminho) {
  $sha = [System.Security.Cryptography.SHA256]::Create()
  $fluxo = [System.IO.File]::OpenRead($Caminho)
  try {
    $bytes = $sha.ComputeHash($fluxo)
  } finally {
    $fluxo.Dispose()
    $sha.Dispose()
  }
  return ([System.BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}

try {
  # Tudo com .NET puro: Get-Content/Where-Object/Get-ChildItem/Split-Path/
  # Copy-Item nao foram provados no ambiente dos instaladores (mesma familia
  # do Get-FileHash que falhou no CI da matriz).
  $manifesto = [System.IO.Path]::Combine($Backup, 'MANIFEST.sha256')
  $raizCopia = [System.IO.Path]::Combine($Backup, 'DZ23-Trading-AI')
  if (-not [System.IO.File]::Exists($manifesto) -or
      -not [System.IO.Directory]::Exists($raizCopia)) {
    Add-Linha 'RESULTADO=FALHOU'
    Add-Linha 'MOTIVO=BACKUP_INCOMPLETO'
    Encerrar 2
  }

  if ([System.IO.Directory]::Exists($Destino) -and -not $Forcar) {
    $temConteudo = $false
    try {
      $temConteudo = @([System.IO.Directory]::EnumerateFileSystemEntries($Destino)).Count -gt 0
    } catch { $temConteudo = $false }
    if ($temConteudo) {
      Add-Linha 'RESULTADO=FALHOU'
      Add-Linha 'MOTIVO=DESTINO_EXISTENTE_SEM_FORCAR'
      Encerrar 2
    }
  }

  $entradas = New-Object System.Collections.Generic.List[string]
  foreach ($l in [System.IO.File]::ReadAllLines($manifesto)) {
    if ($l -match '^\S+ \*') { $entradas.Add($l) | Out-Null }
  }
  if ($entradas.Count -eq 0) {
    Add-Linha 'RESULTADO=FALHOU'
    Add-Linha 'MOTIVO=MANIFEST_VAZIO'
    Encerrar 2
  }

  # 1) Valida o backup antes de qualquer escrita.
  foreach ($linha in $entradas) {
    $partes = $linha -split ' \*', 2
    $esperado = $partes[0].ToLowerInvariant()
    $rel = $partes[1]
    $caminho = [System.IO.Path]::Combine($raizCopia, $rel.Replace('/', '\'))
    if (-not [System.IO.File]::Exists($caminho)) {
      Add-Linha 'RESULTADO=FALHOU'
      Add-Linha ('MOTIVO=BACKUP_FALTANDO:' + $rel)
      Encerrar 2
    }
    if ((Hash-Sha256 $caminho) -ne $esperado) {
      Add-Linha 'RESULTADO=FALHOU'
      Add-Linha ('MOTIVO=BACKUP_CORROMPIDO:' + $rel)
      Encerrar 2
    }
  }

  # 2) Restaura e confere o destino contra o manifest.
  [System.IO.Directory]::CreateDirectory($Destino) | Out-Null
  $restaurados = 0
  foreach ($linha in $entradas) {
    $partes = $linha -split ' \*', 2
    $esperado = $partes[0].ToLowerInvariant()
    $rel = $partes[1]
    $origem = [System.IO.Path]::Combine($raizCopia, $rel.Replace('/', '\'))
    $alvo = [System.IO.Path]::Combine($Destino, $rel.Replace('/', '\'))
    [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($alvo)) | Out-Null
    [System.IO.File]::Copy($origem, $alvo, $true)
    if ((Hash-Sha256 $alvo) -ne $esperado) {
      Add-Linha 'RESULTADO=FALHOU'
      Add-Linha ('MOTIVO=RESTAURACAO_DIVERGENTE:' + $rel)
      Encerrar 2
    }
    $restaurados++
  }

  Add-Linha 'RESULTADO=OK'
  Add-Linha ('ARQUIVOS=' + $restaurados)
  Add-Linha ('DESTINO=' + $Destino)
  Write-Saida
  exit 0
} catch {
  Add-Linha 'RESULTADO=FALHOU'
  Add-Linha ('MOTIVO=' + $_.Exception.Message)
  Encerrar 2
}
