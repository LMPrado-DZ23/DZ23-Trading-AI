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

function Hash-Sha256([string]$Caminho) {
  return (Get-FileHash -LiteralPath $Caminho -Algorithm SHA256).Hash.ToLowerInvariant()
}

try {
  $manifesto = Join-Path $Backup 'MANIFEST.sha256'
  $raizCopia = Join-Path $Backup 'DZ23-Trading-AI'
  if (-not (Test-Path -LiteralPath $manifesto) -or -not (Test-Path -LiteralPath $raizCopia)) {
    Add-Linha 'RESULTADO=FALHOU'
    Add-Linha 'MOTIVO=BACKUP_INCOMPLETO'
    Encerrar 2
  }

  if ((Test-Path -LiteralPath $Destino) -and -not $Forcar) {
    $conteudo = Get-ChildItem -LiteralPath $Destino -Force -ErrorAction SilentlyContinue
    if ($conteudo) {
      Add-Linha 'RESULTADO=FALHOU'
      Add-Linha 'MOTIVO=DESTINO_EXISTENTE_SEM_FORCAR'
      Encerrar 2
    }
  }

  $entradas = Get-Content -LiteralPath $manifesto | Where-Object { $_ -match '^\S+ \*' }
  if (-not $entradas) {
    Add-Linha 'RESULTADO=FALHOU'
    Add-Linha 'MOTIVO=MANIFEST_VAZIO'
    Encerrar 2
  }

  # 1) Valida o backup antes de qualquer escrita.
  foreach ($linha in $entradas) {
    $partes = $linha -split ' \*', 2
    $esperado = $partes[0].ToLowerInvariant()
    $rel = $partes[1]
    $caminho = Join-Path $raizCopia ($rel.Replace('/', '\'))
    if (-not (Test-Path -LiteralPath $caminho)) {
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
  New-Item -ItemType Directory -Path $Destino -Force | Out-Null
  $restaurados = 0
  foreach ($linha in $entradas) {
    $partes = $linha -split ' \*', 2
    $esperado = $partes[0].ToLowerInvariant()
    $rel = $partes[1]
    $origem = Join-Path $raizCopia ($rel.Replace('/', '\'))
    $alvo = Join-Path $Destino ($rel.Replace('/', '\'))
    New-Item -ItemType Directory -Path (Split-Path -Parent $alvo) -Force | Out-Null
    Copy-Item -LiteralPath $origem -Destination $alvo -Force
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
