; DZ23 Trading AI — instalador / atualizador Windows (Inno Setup 6)
;
; Garantias deste instalador (auditoria 2026-10-14):
; - AppId PINADO em "DZ23 Trading AI": as versoes 0.3.x/0.4.0 ja gravaram a
;   chave "DZ23 Trading AI_is1" com este AppId (nome). Muda-lo criaria uma
;   SEGUNDA entrada no Windows — proibido.
; - Detecta a versao instalada e informa o dono ANTES de alterar qualquer
;   arquivo (Atualizar / Reparar-reinstalar / Cancelar; downgrade bloqueado).
; - Encerra SOMENTE o proprio DZ23, de forma graciosa (--quit). Nunca
;   taskkill, nunca Restart Manager, nunca OmniRoute/Ollama/terceiros.
; - Backup VERIFICADO (hash a hash) dos dados em %APPDATA%\DZ23-Trading-AI
;   antes de qualquer atualizacao/downgrade forcado; rollback via script.
; - Nenhum dado do usuario e gravado na pasta de instalacao; desinstalar
;   NUNCA apaga %APPDATA%\DZ23-Trading-AI (dados financeiros).
; - Automacao: /VERYSILENT com /DZ23STATUS=<arquivo> grava o resultado;
;   recusas retornam codigo nao-zero e NADA e alterado. /SIMULAR=1 e
;   dry-run LOCAL: detecta, mostra o que faria e aborta sem escrever nada.
;
; AppVersion vem do build: ISCC /DAppVersion=<versao do pyproject.toml>.
; A versao nunca e duplicada a mao aqui. Mensagens em ASCII de proposito:
; o script compila igual em qualquer codepage do Windows.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "DZ23 Trading AI"
#define AppPublisher "DZ23"
#define AppExeName "DZ23-Trading-AI.exe"
#define AppIdConst "DZ23 Trading AI"

[Setup]
; AppId = nome estavel (NAO mudar sem estrategia de migracao — missao item 2).
AppId={#AppIdConst}
AppName={#AppName}
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\DZ23-Trading-AI
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=DZ23-Trading-AI-Setup-v{#AppVersion}
SetupIconFile=..\..\service\static\marca\dz23-icone.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2
SolidCompression=yes
; NUNCA fechar programas via Restart Manager: o encerramento do DZ23 e feito
; pelo nosso contrato (--quit), e terceiros jamais sao tocados.
CloseApplications=no
RestartApplications=no
; Um setup por vez (duas execucoes simultaneas do instalador nao competem).
SetupMutex=DZ23-Trading-AI-Setup
MinVersion=10.0

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Files]
Source: "..\..\dist\DZ23-Trading-AI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Scripts auxiliares rodam de {tmp} (nunca instalados no disco do usuario).
Source: "dz23_processo.ps1"; DestDir: "{tmp}"; Flags: dontcopy
Source: "backup_dados.ps1"; DestDir: "{tmp}"; Flags: dontcopy

[InstallDelete]
; Bundle antigo do PyInstaller: evita DLLs orfas de versoes anteriores.
; SOMENTE o diretorio interno do bundle — nada de dados do usuario aqui.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; AppUserModelID: "DZ23 Trading AI"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; AppUserModelID: "DZ23 Trading AI"

[Registry]
; Identidade do app no Windows (AUMID): sem este registro o toast sai sem
; nome próprio e o app não aparece na lista de remetentes das Configurações
; (achado N01 do dono). Só mexe na chave do PRÓPRIO DZ23.
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\DZ23 Trading AI"; ValueType: string; ValueName: "DisplayName"; ValueData: "DZ23 Trading AI"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\AppUserModelId\DZ23 Trading AI"; ValueType: string; ValueName: "IconUri"; ValueData: "{app}\{#AppExeName}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Abrir o {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
const
  CHAVE_UNINSTALL = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\DZ23 Trading AI_is1';
  PONTO_INICIAL = 8000;
  PONTO_FINAL = 8010;

var
  VersaoInstalada: String;
  VersaoNova: String;
  Cenario: String;
  DetalheCena: String;
  Simular: Boolean;
  Silencioso: Boolean;
  CaminhoStatus: String;
  ScriptProcesso: String;
  ScriptBackup: String;

function GravarStatus(const Linha: String): Boolean;
begin
  Result := True;
  if CaminhoStatus = '' then
    Exit;
  if not SaveStringToFile(CaminhoStatus, Linha + #13#10, True) then
    Result := False;
end;

function GravarStatusLogico(const Chave: String; Valor: Boolean): Boolean;
begin
  if Valor then
    Result := GravarStatus(Chave + '=1')
  else
    Result := GravarStatus(Chave + '=0');
end;

function LerLinhas(const Caminho: String): TArrayOfString;
begin
  if not LoadStringsFromFile(Caminho, Result) then
    SetArrayLength(Result, 0);
end;

function ValorEm(const Linhas: TArrayOfString; const Chave: String): String;
var
  i: Integer;
  Prefixo: String;
begin
  Result := '';
  Prefixo := Chave + '=';
  for i := 0 to GetArrayLength(Linhas) - 1 do
    if Copy(Linhas[i], 1, Length(Prefixo)) = Prefixo then
    begin
      Result := Copy(Linhas[i], Length(Prefixo) + 1, Length(Linhas[i]));
      Exit;
    end;
end;

{ ---- comparacao de versoes x.y.z (numerica; ausente conta como 0) ---- }
function ParteVersao(const S: String; Indice: Integer): Integer;
var
  i, Contador, Inicio: Integer;
begin
  Result := 0;
  Contador := 0;
  Inicio := 1;
  for i := 1 to Length(S) + 1 do
  begin
    if (i > Length(S)) or (S[i] = '.') then
    begin
      if Contador = Indice then
      begin
        if i > Inicio then
          Result := StrToIntDef(Copy(S, Inicio, i - Inicio), 0);
        Exit;
      end;
      Contador := Contador + 1;
      Inicio := i + 1;
    end;
  end;
end;

function CompararVersoes(const A, B: String): Integer;
var
  i, Pa, Pb: Integer;
begin
  for i := 0 to 3 do
  begin
    Pa := ParteVersao(A, i);
    Pb := ParteVersao(B, i);
    if Pa < Pb then
    begin
      Result := -1;
      Exit;
    end;
    if Pa > Pb then
    begin
      Result := 1;
      Exit;
    end;
  end;
  Result := 0;
end;

{ ---- deteccao no registro (a MESMA chave que o 0.4.0 do dono usa) ---- }
function VersaoNoRegistro(): String;
var
  V: String;
begin
  Result := '';
  if RegQueryStringValue(HKCU, CHAVE_UNINSTALL, 'DisplayVersion', V) then
    Result := V
  else if RegQueryStringValue(HKLM, CHAVE_UNINSTALL, 'DisplayVersion', V) then
    Result := V;
end;

{ ---- processos: script PowerShell (somente leitura do nosso app) ---- }
function RodarPowerShell(const Script, Argumentos, Saida: String): Boolean;
var
  Codigo: Integer;
  Linha: String;
begin
  Linha := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + Script + '" ' + Argumentos +
           ' -SaidaArquivo "' + Saida + '"';
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Linha, '',
                 SW_HIDE, ewWaitUntilTerminated, Codigo);
end;

function DetectarApp(var Rodando: Boolean; var Porta: Integer; var MutexApp: Boolean): Boolean;
var
  Saida: String;
  Linhas: TArrayOfString;
begin
  Rodando := False;
  Porta := 0;
  MutexApp := False;
  Saida := ExpandConstant('{tmp}\dz23-status.txt');
  DeleteFile(Saida);
  if not RodarPowerShell(ScriptProcesso,
      '-Acao Status -Exe "' + ExpandConstant('{app}\{#AppExeName}') + '"' +
      ' -PortaInicial ' + IntToStr(PONTO_INICIAL) + ' -PortaFinal ' + IntToStr(PONTO_FINAL),
      Saida) then
  begin
    Result := False;
    Exit;
  end;
  Linhas := LerLinhas(Saida);
  Rodando := ValorEm(Linhas, 'RODANDO') = '1';
  Porta := StrToIntDef(ValorEm(Linhas, 'PORTA'), 0);
  MutexApp := ValorEm(Linhas, 'MUTEX') = '1';
  GravarStatusLogico('APP_RODANDO', Rodando);
  GravarStatusLogico('APP_MUTEX', MutexApp);
  if Porta > 0 then
    GravarStatus('APP_PORTA=' + IntToStr(Porta));
  Result := True;
end;

function FecharAppGracioso(): Boolean;
var
  Saida: String;
  Linhas: TArrayOfString;
begin
  Saida := ExpandConstant('{tmp}\dz23-fechar.txt');
  DeleteFile(Saida);
  RodarPowerShell(ScriptProcesso,
    '-Acao FecharGracioso -Exe "' + ExpandConstant('{app}\{#AppExeName}') + '"' +
    ' -PortaInicial ' + IntToStr(PONTO_INICIAL) + ' -PortaFinal ' + IntToStr(PONTO_FINAL) +
    ' -TimeoutSegundos 60',
    Saida);
  Linhas := LerLinhas(Saida);
  Result := ValorEm(Linhas, 'RESULTADO') = 'FECHADO';
end;

function FazerBackup(): Boolean;
var
  Saida: String;
  Linhas: TArrayOfString;
  Origem, DestinoBase, Resultado: String;
begin
  Result := False;
  Origem := ExpandConstant('{userappdata}\DZ23-Trading-AI');
  DestinoBase := ExpandConstant('{userappdata}\DZ23-Trading-AI-backups');
  Saida := ExpandConstant('{tmp}\dz23-backup.txt');
  DeleteFile(Saida);
  RodarPowerShell(ScriptBackup,
    '-De "' + VersaoInstalada + '" -Para "' + VersaoNova + '"' +
    ' -Origem "' + Origem + '" -DestinoBase "' + DestinoBase + '"',
    Saida);
  Linhas := LerLinhas(Saida);
  Resultado := ValorEm(Linhas, 'RESULTADO');
  GravarStatus('BACKUP=' + Resultado);
  if ValorEm(Linhas, 'MOTIVO') <> '' then
    GravarStatus('BACKUP_MOTIVO=' + ValorEm(Linhas, 'MOTIVO'));
  if ValorEm(Linhas, 'DESTINO') <> '' then
    GravarStatus('BACKUP_DESTINO=' + ValorEm(Linhas, 'DESTINO'));
  Result := (Resultado = 'OK') or (Resultado = 'SEM_DADOS');
end;

function PrepararAmbiente(): Boolean;
begin
  Result := True;
  ExtractTemporaryFile('dz23_processo.ps1');
  ExtractTemporaryFile('backup_dados.ps1');
  ScriptProcesso := ExpandConstant('{tmp}\dz23_processo.ps1');
  ScriptBackup := ExpandConstant('{tmp}\backup_dados.ps1');
end;

procedure DefinirCenario();
var
  Comparacao: Integer;
begin
  DetalheCena := '';
  if VersaoInstalada = '' then
  begin
    Cenario := 'INSTALACAO_NOVA';
    DetalheCena := 'instalacao nova (nenhuma versao registrada)';
    Exit;
  end;
  Comparacao := CompararVersoes(VersaoInstalada, VersaoNova);
  if Comparacao = 0 then
  begin
    Cenario := 'REPARO';
    DetalheCena := 'reparar/reinstalar a mesma versao ' + VersaoNova;
  end
  else if Comparacao < 0 then
  begin
    Cenario := 'ATUALIZACAO';
    DetalheCena := 'atualizar de ' + VersaoInstalada + ' para ' + VersaoNova +
                   ' (backup verificado antes de continuar)';
  end
  else
  begin
    Cenario := 'DOWNGRADE';
    DetalheCena := 'downgrade de ' + VersaoInstalada + ' para ' + VersaoNova;
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Rodando, MutexApp, ForcarDowngrade, Novo: Boolean;
  Porta, Resposta: Integer;
  TextoInstalada, TextoRodando: String;
begin
  Result := '';
  NeedsRestart := False;
  { -- estado de telemetria para a automacao (CI/evidencias) -- }
  VersaoNova := '{#AppVersion}';
  VersaoInstalada := VersaoNoRegistro();
  Silencioso := WizardSilent();
  CaminhoStatus := ExpandConstant('{param:DZ23STATUS|}');
  Simular := ExpandConstant('{param:SIMULAR|0}') <> '0';
  ForcarDowngrade := ExpandConstant('{param:FORCEDOWNGRADE|0}') <> '0';
  GravarStatus('INSTALADOR={#AppIdConst}');
  GravarStatus('VERSAO_NOVA=' + VersaoNova);
  GravarStatus('VERSAO_INSTALADA=' + VersaoInstalada);
  GravarStatusLogico('SILENCIOSO', Silencioso);
  if VersaoInstalada = '' then
    TextoInstalada := 'nenhuma'
  else
    TextoInstalada := VersaoInstalada;

  if not PrepararAmbiente() then
  begin
    GravarStatus('RESULTADO=FALHA_INTERNA_SEM_SCRIPTS');
    Result := 'Falha interna: scripts do instalador nao puderam ser extraidos. Nada foi alterado.';
    Exit;
  end;

  { -- deteccao de instancia em execucao (somente o NOSSO app) -- }
  if not DetectarApp(Rodando, Porta, MutexApp) then
  begin
    GravarStatus('RESULTADO=FALHA_DETECCAO_PROCESSO');
    Result := 'Nao foi possivel verificar se o DZ23 Trading AI esta aberto. Nada foi alterado. ' +
              'Feche o aplicativo e execute o instalador novamente.';
    Exit;
  end;

  DefinirCenario();
  GravarStatus('CENARIO=' + Cenario);

  { -- /SIMULAR=1: evidencia local sem alterar NADA (nem fechar o app) -- }
  if Simular then
  begin
    if Rodando then
      TextoRodando := 'sim (porta ' + IntToStr(Porta) + ')'
    else
      TextoRodando := 'nao';
    GravarStatus('RESULTADO=SIMULADO');
    if not Silencioso then
      MsgBox('SIMULACAO (/SIMULAR=1) — nada sera alterado.' + #13#10#13#10 +
             'Versao instalada encontrada: ' + TextoInstalada + #13#10 +
             'Versao deste instalador: ' + VersaoNova + #13#10 +
             'Aplicativo em execucao: ' + TextoRodando + #13#10#13#10 +
             'O que aconteceria no modo real:' + #13#10 + DetalheCena + #13#10#13#10 +
             'A simulacao termina aqui, sem gravar arquivos nem registro.',
             mbInformation, MB_OK);
    Result := 'SIMULACAO (/SIMULAR=1): instalacao cancelada de proposito. Nada foi alterado.';
    Exit;
  end;

  { -- cenario por versao: Atualizar / Reparar / Downgrade / Instalar -- }
  if Cenario = 'REPARO' then
  begin
    if not Silencioso then
      if MsgBox('O DZ23 Trading AI ' + VersaoInstalada + ' ja esta instalado.' + #13#10#13#10 +
                'Deseja REPARAR (reinstalar os arquivos do programa)?' + #13#10 +
                'Seus dados em %APPDATA%\DZ23-Trading-AI sao preservados.',
                mbConfirmation, MB_YESNO) = IDNO then
      begin
        GravarStatus('RESULTADO=ABORTADO_PELO_DONO');
        Result := 'Reparo cancelado. Nada foi alterado.';
        Exit;
      end;
  end
  else if Cenario = 'ATUALIZACAO' then
  begin
    if not Silencioso then
      if MsgBox('Foi detectada uma instalacao existente:' + #13#10#13#10 +
                '- DZ23 Trading AI ' + VersaoInstalada + #13#10 +
                '- em ' + ExpandConstant('{app}') + #13#10#13#10 +
                'Este instalador ATUALIZA para a versao ' + VersaoNova + '.' + #13#10 +
                'Somente o programa e substituido. Seus dados (incluindo os financeiros) em ' +
                '%APPDATA%\DZ23-Trading-AI sao preservados — e um backup VERIFICADO e criado ' +
                'antes de continuar.' + #13#10#13#10 +
                'Atualizar agora?', mbConfirmation, MB_YESNO) = IDNO then
      begin
        GravarStatus('RESULTADO=ABORTADO_PELO_DONO');
        Result := 'Atualizacao cancelada. Nada foi alterado.';
        Exit;
      end;
  end
  else if Cenario = 'DOWNGRADE' then
  begin
    if not ForcarDowngrade then
    begin
      GravarStatus('RESULTADO=ABORTADO_DOWNGRADE');
      if not Silencioso then
        MsgBox('A versao instalada (' + VersaoInstalada + ') e MAIS NOVA que a deste ' +
               'instalador (' + VersaoNova + ').' + #13#10#13#10 +
               'Downgrade pode incompatibilizar dados e nao e permitido sem confirmacao ' +
               'especifica. NADA foi alterado.' + #13#10#13#10 +
               'Se e realmente isso que voce quer, rode o instalador com o parametro ' +
               '/FORCEDOWNGRADE=1 (um backup verificado sera criado antes).',
               mbError, MB_OK);
      Result := 'Downgrade bloqueado (instalada ' + VersaoInstalada + ' > instalador ' +
                VersaoNova + '). Nada foi alterado.';
      Exit;
    end;
    if not Silencioso then
      if MsgBox('CONFIRMACAO ESPECIFICA DE DOWNGRADE' + #13#10#13#10 +
                'Rebaixar de ' + VersaoInstalada + ' para ' + VersaoNova + '?' + #13#10 +
                'Um backup verificado dos dados sera criado antes.', mbError, MB_YESNO) = IDNO then
      begin
        GravarStatus('RESULTADO=ABORTADO_PELO_DONO');
        Result := 'Downgrade cancelado. Nada foi alterado.';
        Exit;
      end;
  end;

  { -- encerramento SEGURO do proprio app, se aberto -- }
  if Rodando then
  begin
    if MutexApp then
    begin
      if not Silencioso then
        if MsgBox('O DZ23 Trading AI esta aberto (porta ' + IntToStr(Porta) + ').' + #13#10#13#10 +
                  'Para continuar, ele sera FECHADO com seguranca — somente o DZ23, ' +
                  'nunca outros programas. Continuar?', mbConfirmation, MB_YESNO) = IDNO then
        begin
          GravarStatus('RESULTADO=ABORTADO_PELO_DONO');
          Result := 'Instalacao cancelada com o aplicativo aberto. Nada foi alterado.';
          Exit;
        end;
      if not FecharAppGracioso() then
      begin
        GravarStatus('RESULTADO=ABORTADO_APP_NAO_FECHOU');
        Result := 'O DZ23 Trading AI nao encerrou a tempo. Feche-o pelo painel ' +
                  '(botao "Fechar aplicativo") e execute o instalador novamente. Nada foi alterado.';
        Exit;
      end;
      GravarStatus('APP_FECHADO=GRACIOSO');
    end
    else
    begin
      { Instancia antiga (sem contrato de fechamento automatico). }
      if Silencioso then
      begin
        GravarStatus('RESULTADO=ABORTADO_APP_ABERTO_ANTIGO');
        Result := 'O DZ23 Trading AI esta aberto e esta versao instalada nao permite ' +
                  'fechamento automatico. Feche o aplicativo e execute o instalador novamente. ' +
                  'Nada foi alterado.';
        Exit;
      end;
      while Rodando do
      begin
        Resposta := MsgBox('O DZ23 Trading AI ' + VersaoInstalada + ' esta aberto.' + #13#10#13#10 +
                     'Feche o aplicativo pelo painel (botao "Fechar aplicativo") e clique em ' +
                     'Repetir. Se precisar, encerre o processo DZ23-Trading-AI.exe pelo ' +
                     'Gerenciador de Tarefas.' + #13#10#13#10 +
                     'O instalador NUNCA encerra outros programas.',
                     mbError, MB_RETRYCANCEL);
        if Resposta = IDCANCEL then
        begin
          GravarStatus('RESULTADO=ABORTADO_APP_ABERTO_ANTIGO');
          Result := 'Instalacao cancelada com o aplicativo aberto. Nada foi alterado.';
          Exit;
        end;
        if not DetectarApp(Rodando, Porta, MutexApp) then
        begin
          GravarStatus('RESULTADO=FALHA_DETECCAO_PROCESSO');
          Result := 'Nao foi possivel verificar se o DZ23 Trading AI esta aberto. Nada foi alterado.';
          Exit;
        end;
      end;
      GravarStatus('APP_FECHADO=MANUAL');
    end;
  end;

  { -- backup verificado antes de trocar de versao -- }
  Novo := VersaoInstalada = '';
  if (not Novo) and (CompararVersoes(VersaoInstalada, VersaoNova) <> 0) then
  begin
    if not FazerBackup() then
    begin
      GravarStatus('RESULTADO=ABORTADO_BACKUP_FALHOU');
      Result := 'O backup verificado dos dados falhou — instalacao abortada para proteger ' +
                'seus dados. Nada foi alterado. Detalhes em ' +
                '%APPDATA%\DZ23-Trading-AI-backups.';
      Exit;
    end;
  end;

  GravarStatus('RESULTADO=PROSSEGUINDO');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssDone then
  begin
    GravarStatus('RESULTADO=OK');
    GravarStatus('VERSAO_FINAL={#AppVersion}');
  end;
end;
