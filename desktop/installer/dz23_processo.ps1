# DZ23 Trading AI - deteccao e encerramento SEGURO do proprio aplicativo.
# Somente leitura do estado + pedido de saida graciosa (--quit) no PROPRIO exe.
# Nunca encerra outros programas; nunca taskkill.
#
# Saida: arquivo KEY=VALUE (ASCII) que o instalador Inno Setup le.
# Acoes:
#   Status         -> RODANDO=0/1, MUTEX=0/1, PORTA=n, VERSAO_INSTANCIA=x
#   FecharGracioso -> executa o exe com --quit e espera sair;
#                     RESULTADO=FECHADO | NAO_FECHADO | JA_ENCERRADO
[CmdletBinding()]
param(
  [ValidateSet('Status','FecharGracioso')]
  [string]$Acao = 'Status',
  [string]$Exe = "$env:LOCALAPPDATA\DZ23-Trading-AI\DZ23-Trading-AI.exe",
  [int]$PortaInicial = 8000,
  [int]$PortaFinal = 8010,
  [int]$TimeoutSegundos = 60,
  [string]$SaidaArquivo = ''
)

$ErrorActionPreference = 'Continue'
$script:LINHAS = New-Object System.Collections.Generic.List[string]

function Add-Linha([string]$Texto) { $script:LINHAS.Add($Texto) | Out-Null }

function Write-Saida() {
  if ($SaidaArquivo -ne '') {
    [System.IO.File]::WriteAllLines($SaidaArquivo, $script:LINHAS,
      (New-Object System.Text.UTF8Encoding($false)))
  }
  foreach ($l in $script:LINHAS) { Write-Output $l }
}

function Test-MutexApp() {
  try {
    $m = [System.Threading.Mutex]::OpenExisting('Local\DZ23-Trading-AI-Instancia')
    $m.Dispose()
    return $true
  } catch { return $false }
}

# True somente quando o /healthz responde com o marcador do DZ23.
function Test-PortaDz23([int]$Porta, [ref]$Versao) {
  $cliente = $null
  try {
    $cliente = New-Object System.Net.Sockets.TcpClient
    $tarefa = $cliente.ConnectAsync('127.0.0.1', $Porta)
    if (-not $tarefa.Wait(700)) { return $false }
    $fluxo = $cliente.GetStream()
    $fluxo.ReadTimeout = 2000
    $fluxo.WriteTimeout = 2000
    $pedido = [System.Text.Encoding]::ASCII.GetBytes(
      "GET /healthz HTTP/1.0`r`nHost: 127.0.0.1`r`nConnection: close`r`n`r`n")
    $fluxo.Write($pedido, 0, $pedido.Length)
    $sb = New-Object System.Text.StringBuilder
    $buffer = New-Object byte[] 4096
    while ($true) {
      $lido = $fluxo.Read($buffer, 0, $buffer.Length)
      if ($lido -le 0) { break }
      [void]$sb.Append([System.Text.Encoding]::UTF8.GetString($buffer, 0, $lido))
      if ($sb.Length -ge 65536) { break }
      if (-not $fluxo.DataAvailable) {
        Start-Sleep -Milliseconds 120
        if (-not $fluxo.DataAvailable) { break }
      }
    }
    $texto = $sb.ToString()
    if ($texto -notmatch 'tradingagents-agency') { return $false }
    $achado = [regex]::Match($texto, '"versao"\s*:\s*"([^"]+)"')
    if ($achado.Success) { $Versao.Value = $achado.Groups[1].Value }
    return $true
  } catch { return $false }
  finally { if ($cliente) { $cliente.Close() } }
}

function Get-Estado() {
  $mutex = Test-MutexApp
  $porta = 0
  $versao = ''
  for ($p = $PortaInicial; $p -le $PortaFinal; $p++) {
    $v = ''
    if (Test-PortaDz23 $p ([ref]$v)) { $porta = $p; $versao = $v; break }
  }
  # Mutex presente tambem conta como instancia viva (subindo, healthz ainda nao).
  $rodando = ($porta -ne 0) -or $mutex
  return @{ Rodando = $rodando; Porta = $porta; Mutex = $mutex; Versao = $versao }
}

switch ($Acao) {
  'Status' {
    $e = Get-Estado
    Add-Linha ("RODANDO=" + [int][bool]$e.Rodando)
    Add-Linha ("MUTEX=" + [int][bool]$e.Mutex)
    Add-Linha ("PORTA=" + $e.Porta)
    if ($e.Versao -ne '') { Add-Linha ("VERSAO_INSTANCIA=" + $e.Versao) }
    Write-Saida
    exit 0
  }
  'FecharGracioso' {
    $e = Get-Estado
    if (-not $e.Rodando) {
      Add-Linha 'RESULTADO=JA_ENCERRADO'
      Write-Saida
      exit 0
    }
    if (-not $e.Mutex) {
      # Instancia antiga: sem contrato --quit. O instalador pede o fechamento
      # manual; o script NUNCA tenta matar nada.
      Add-Linha 'RESULTADO=NAO_FECHADO'
      Add-Linha 'MOTIVO=INSTANCIA_ANTIGA_SEM_CONTRATO'
      Write-Saida
      exit 0
    }
    if (Test-Path -LiteralPath $Exe) {
      $ajudante = Start-Process -FilePath $Exe -ArgumentList '--quit' -PassThru -WindowStyle Hidden
      try { $ajudante | Wait-Process -Timeout 30 -ErrorAction Stop } catch { }
    }
    $limite = (Get-Date).AddSeconds($TimeoutSegundos)
    $fechou = $false
    while ((Get-Date) -lt $limite) {
      if (-not (Test-MutexApp)) { $fechou = $true; break }
      Start-Sleep -Milliseconds 500
    }
    if ($fechou -and $e.Porta -ne 0) {
      for ($i = 0; $i -lt 40; $i++) {
        $v = ''
        if (-not (Test-PortaDz23 $e.Porta ([ref]$v))) { break }
        Start-Sleep -Milliseconds 250
      }
    }
    if ($fechou) {
      Add-Linha 'RESULTADO=FECHADO'
    } else {
      Add-Linha 'RESULTADO=NAO_FECHADO'
      Add-Linha 'MOTIVO=TIMEOUT'
    }
    Write-Saida
    exit 0
  }
}

Add-Linha 'RESULTADO=ERRO_ACAO_DESCONHECIDA'
Write-Saida
exit 2
