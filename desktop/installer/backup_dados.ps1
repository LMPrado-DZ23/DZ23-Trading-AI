# DZ23 Trading AI - backup VERIFICADO dos dados antes de atualizar versao.
# Copia %APPDATA%\DZ23-Trading-AI para
#   %APPDATA%\DZ23-Trading-AI-backups\pre-upgrade-<de>-para-<para>-<stamp>\
# e confere arquivo a arquivo por SHA256 (origem == copia). Falha => FALHOU
# (o instalador aborta: dados valem mais que a atualizacao). Nunca apaga os
# dados originais; poda apenas backups antigos (mantem os 5 mais recentes).
#
# Saida KEY=VALUE (ASCII): RESULTADO=OK|SEM_DADOS|FALHOU, DESTINO, ARQUIVOS,
# BYTES, MOTIVO.
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$De,
  [Parameter(Mandatory=$true)][string]$Para,
  [string]$Origem = "$env:APPDATA\DZ23-Trading-AI",
  [string]$DestinoBase = "$env:APPDATA\DZ23-Trading-AI-backups",
  [int]$Manter = 5,
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

# O log do instalador fica AO LADO dos backups — inclusive em FALHA. A
# mensagem do instalador promete "detalhes em %APPDATA%\...-backups"; sem
# isto, a falha morria junto com o arquivo temporario do Inno.
function Gravar-LogNoDestino([string]$Sufixo) {
  try {
    New-Item -ItemType Directory -Path $DestinoBase -Force | Out-Null
    $stampLog = (Get-Date).ToString('yyyyMMdd-HHmmss')
    $arquivo = Join-Path $DestinoBase "instalador-backup-$stampLog.$Sufixo.log"
    [System.IO.File]::WriteAllLines($arquivo, $script:LINHAS,
      (New-Object System.Text.UTF8Encoding($false)))
    return $arquivo
  } catch { return '' }
}

function Caminho-Relativo([string]$Raiz, [string]$Completo) {
  $rel = $Completo.Substring($Raiz.Length + 1)
  return $rel.Replace('\', '/')
}

try {
  if (-not (Test-Path -LiteralPath $Origem)) {
    Add-Linha 'RESULTADO=SEM_DADOS'
    Add-Linha 'MOTIVO=ORIGEM_INEXISTENTE'
    Encerrar 0
  }

  $stamp = (Get-Date).ToString('yyyyMMdd-HHmmss')
  $nome = "pre-upgrade-$De-para-$Para-$stamp"
  $destino = Join-Path $DestinoBase $nome
  $raizCopia = Join-Path $destino 'DZ23-Trading-AI'
  New-Item -ItemType Directory -Path $destino -Force | Out-Null

  Copy-Item -LiteralPath $Origem -Destination $raizCopia -Recurse -Force

  # Manifest + verificacao origem == copia (hash a hash).
  $origemFull = (Get-Item -LiteralPath $Origem).FullName
  $arquivos = Get-ChildItem -LiteralPath $Origem -Recurse -File -Force
  $linhasManifest = New-Object System.Collections.Generic.List[string]
  $totalBytes = 0
  foreach ($f in $arquivos) {
    $rel = Caminho-Relativo $origemFull $f.FullName
    $copia = Join-Path $raizCopia ($rel.Replace('/', '\'))
    if (-not (Test-Path -LiteralPath $copia)) {
      Add-Linha 'RESULTADO=FALHOU'
      Add-Linha ('MOTIVO=ARQUIVO_FALTANDO:' + $rel)
      Gravar-LogNoDestino 'FALHA' | Out-Null
      Encerrar 2
    }
    $hOrigem = Hash-Sha256 $f.FullName
    $hCopia = Hash-Sha256 $copia
    if ($hOrigem -ne $hCopia) {
      Add-Linha 'RESULTADO=FALHOU'
      Add-Linha ('MOTIVO=HASH_DIVERGENTE:' + $rel)
      Gravar-LogNoDestino 'FALHA' | Out-Null
      Encerrar 2
    }
    $linhasManifest.Add("$hOrigem *$rel") | Out-Null
    $totalBytes += $f.Length
  }
  [System.IO.File]::WriteAllLines((Join-Path $destino 'MANIFEST.sha256'), $linhasManifest,
    (New-Object System.Text.UTF8Encoding($false)))

  $resumo = [ordered]@{
    ok         = $true
    de         = $De
    para       = $Para
    criado_em  = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    origem     = $origemFull
    destino    = $destino
    arquivos   = $arquivos.Count
    bytes      = $totalBytes
    manifest   = 'MANIFEST.sha256'
  }
  [System.IO.File]::WriteAllText((Join-Path $destino 'backup.json'),
    ($resumo | ConvertTo-Json -Depth 3), (New-Object System.Text.UTF8Encoding($false)))

  # Poda: mantem os $Manter backups mais recentes (somente pre-upgrade-*).
  $antigos = Get-ChildItem -LiteralPath $DestinoBase -Directory -Filter 'pre-upgrade-*' |
    Sort-Object Name -Descending | Select-Object -Skip $Manter
  foreach ($a in $antigos) { Remove-Item -LiteralPath $a.FullName -Recurse -Force }

  Add-Linha 'RESULTADO=OK'
  Add-Linha ('DESTINO=' + $destino)
  Add-Linha ('ARQUIVOS=' + $arquivos.Count)
  Add-Linha ('BYTES=' + $totalBytes)

  # Log proprio do instalador, ao lado dos backups (tambem em FALHA).
  Gravar-LogNoDestino 'OK' | Out-Null

  Write-Saida
  exit 0
} catch {
  Add-Linha 'RESULTADO=FALHOU'
  Add-Linha ('MOTIVO=' + $_.Exception.Message)
  if ($destino -and (Test-Path -LiteralPath $destino)) {
    # Backup incompleto nao pode parecer valido.
    Remove-Item -LiteralPath $destino -Recurse -Force -ErrorAction SilentlyContinue
  }
  Gravar-LogNoDestino 'FALHA' | Out-Null
  Encerrar 2
}
