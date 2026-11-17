; Instalador do ALFRED (issue #49) — Inno Setup 6.
; Gerado por: uv run python scripts/build_installer.py  (depois de scripts/build.py)
;
; - Por usuário, sem privilégios de administrador (%LOCALAPPDATA%\Programs\ALFRED).
; - Tarefas: atalho na área de trabalho, iniciar com o Windows e IA local (Ollama + modelo).
; - Atualização: instalar por cima fecha o ALFRED em execução (Restart Manager).
; - Desinstalação: remove o programa e a inicialização automática; pergunta se apaga os dados
;   do usuário (%LOCALAPPDATA%\ALFRED). Em modo silencioso, só apaga com /REMOVEDATA.
;   O Ollama é um produto separado e continua instalado (desinstale pelo Windows se quiser).

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{8A3C5D21-6F4E-4B9A-9C2D-7E1F0A3B5C68}
AppName=ALFRED
AppVersion={#AppVersion}
AppVerName=ALFRED {#AppVersion}
AppPublisher=ALFRED
DefaultDirName={localappdata}\Programs\ALFRED
DefaultGroupName=ALFRED
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=ALFRED-Setup-{#AppVersion}
SetupIconFile=alfred.ico
UninstallDisplayIcon={app}\alfred.exe
UninstallDisplayName=ALFRED
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; Flags: unchecked
Name: "autostart"; Description: "Iniciar o ALFRED junto com o Windows"
Name: "localai"; Description: "Instalar a IA local (Ollama + modelo de linguagem, ~2 a 5 GB de download)"

[Files]
Source: "..\dist\ALFRED\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\ALFRED"; Filename: "{app}\alfred.exe"
Name: "{userdesktop}\ALFRED"; Filename: "{app}\alfred.exe"; Tasks: desktopicon

[Registry]
; Só para a desinstalação limpar o valor criado pelo próprio ALFRED (configuração "Iniciar com o Windows").
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "ALFRED"; Flags: uninsdeletevalue dontcreatekey

[Run]
; A configuração do ALFRED é quem manda na inicialização automática (ele mesmo grava o registro).
Filename: "{app}\alfred.exe"; Parameters: "--set general.start_with_windows=true"; Tasks: autostart; Flags: runhidden waituntilterminated
Filename: "{app}\alfred.exe"; Parameters: "--set general.start_with_windows=false"; Tasks: not autostart; Flags: runhidden waituntilterminated
Filename: "{app}\alfred.exe"; Parameters: "--setup-ai"; Tasks: localai; StatusMsg: "Preparando a inteligência local..."; Flags: waituntilterminated skipifsilent
Filename: "{app}\alfred.exe"; Description: "Abrir o ALFRED agora"; Flags: nowait postinstall skipifsilent

[Code]
function RemoveDataRequested(): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/REMOVEDATA') = 0 then
      Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
  RemoveData: Boolean;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\ALFRED');
    if not DirExists(DataDir) then
      Exit;
    if UninstallSilent then
      RemoveData := RemoveDataRequested()
    else
      RemoveData := MsgBox(
        'Apagar também os seus dados do ALFRED (memórias, histórico, configurações e modelos de voz)?' + #13#10 +
        'Escolha "Não" para mantê-los caso reinstale depois.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
    if RemoveData then
      DelTree(DataDir, True, True, True);
  end;
end;
