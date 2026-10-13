[Setup]
AppName=DZ23 Trading AI
AppVersion=0.4.0
DefaultDirName={localappdata}\DZ23-Trading-AI
DefaultGroupName=DZ23 Trading AI
UninstallDisplayIcon={app}\DZ23-Trading-AI.exe
Compression=lzma2
SolidCompression=yes
OutputDir=..\..\dist
OutputBaseFilename=DZ23-Trading-AI-Setup-v0.4.0

[Files]
Source: "..\..\dist\DZ23-Trading-AI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\DZ23 Trading AI"; Filename: "{app}\DZ23-Trading-AI.exe"
Name: "{commondesktop}\DZ23 Trading AI"; Filename: "{app}\DZ23-Trading-AI.exe"

[Run]
Filename: "{app}\DZ23-Trading-AI.exe"; Description: "Iniciar DZ23 Trading AI"; Flags: nowait postinstall skipifsilent
