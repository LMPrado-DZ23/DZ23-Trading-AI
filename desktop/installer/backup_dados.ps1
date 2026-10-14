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

# Escapa um valor para JSON sem depender de ConvertTo-Json (nao provado no
# ambiente filho do instalador).
function Json-Esc([string]$Texto) {
  if ([string]::IsNullOrEmpty($Texto)) { return '' }
  return $Texto.Replace('\', '\\').Replace('"', '\"').Replace("`r", '\r').Replace("`n", '\n')
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

  # JSON montado com .NET (ConvertTo-Json nao foi provado no ambiente filho
  # do instalador — mesma familia do Get-FileHash que falhou no CI).
  $criadoEm = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
  $json = '{' +
    '"ok": true,' +
    '"de": "' + (Json-Esc $De) + '",' +
    '"para": "' + (Json-Esc $Para) + '",' +
    '"criado_em": "' + $criadoEm + '",' +
    '"origem": "' + (Json-Esc $origemFull) + '",' +
    '"destino": "' + (Json-Esc $destino) + '",' +
    '"arquivos": ' + $arquivos.Count + ',' +
    '"bytes": ' + $totalBytes + ',' +
    '"manifest": "MANIFEST.sha256"}'
  [System.IO.File]::WriteAllText((Join-Path $destino 'backup.json'), $json,
    (New-Object System.Text.UTF8Encoding($false)))

  # Poda com .NET puro (somente pre-upgrade-*): mantem os $Manter mais
  # RECENTES pela data do diretorio — ordenar por nome nao serve porque o
  # prefixo pre-upgrade-<de>- domina a comparacao quando o "de" muda.
  $dirs = [System.IO.Directory]::GetDirectories($DestinoBase, 'pre-upgrade-*')
  if ($dirs.Count -gt $Manter) {
    $ticks = New-Object 'long[]' $dirs.Count
    for ($i = 0; $i -lt $dirs.Count; $i++) {
      $ticks[$i] = [System.IO.Directory]::GetLastWriteTimeUtc($dirs[$i]).Ticks
    }
    [Array]::Sort($ticks, $dirs)  # ordena os dois arrays juntos (mais antigo 1o)
    $podar = $dirs.Count - $Manter
    for ($i = 0; $i -lt $podar; $i++) {
      try { [System.IO.Directory]::Delete($dirs[$i], $true) } catch { }
    }
  }

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
  if ($destino -and [System.IO.Directory]::Exists($destino)) {
    # Backup incompleto nao pode parecer valido.
    try { [System.IO.Directory]::Delete($destino, $true) } catch { }
  }
  Gravar-LogNoDestino 'FALHA' | Out-Null
  Encerrar 2
}
