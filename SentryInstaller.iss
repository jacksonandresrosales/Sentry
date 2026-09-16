#define MyAppName "Sentry"
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0-beta.7"
#endif
#ifndef MyAppFileVersion
  #define MyAppFileVersion "0.1.0.7"
#endif
#define MyAppPublisher "Ecuaconexión"
#define MyAppExeName "Sentry.exe"

[Setup]
AppId={{90B31139-4374-4B90-AD5E-1AD961417361}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Ecuaconexion\Sentry
DefaultGroupName=Ecuaconexión\Sentry
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=Sentry_Setup_{#MyAppVersion}
SetupIconFile=app\ui\assets\sentry-installer-icon.ico
VersionInfoVersion={#MyAppFileVersion}
VersionInfoProductVersion={#MyAppFileVersion}
VersionInfoTextVersion={#MyAppVersion}
VersionInfoProductName={#MyAppName}
VersionInfoDescription=Instalador de {#MyAppName}
VersionInfoCompany={#MyAppPublisher}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "dist\SentryApp\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Sentry"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; AppUserModelID: "Ecuaconexion.Sentry"
Name: "{autodesktop}\Sentry"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; AppUserModelID: "Ecuaconexion.Sentry"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir Sentry"; Flags: nowait postinstall skipifsilent; Check: not IsSentryUpdate
Filename: "{app}\{#MyAppExeName}"; Flags: nowait; Check: IsSentryUpdate

[Code]
var
  InstallationLock: THandle;

function OpenProcess(Access: LongWord; InheritHandle: Boolean; ProcessId: LongWord): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function WaitForSingleObject(Handle: THandle; Milliseconds: LongWord): LongWord;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';
function LastProcessError: LongWord;
  external 'GetLastError@kernel32.dll stdcall';
function CreateApplicationMutex(Attributes: Integer; InitialOwner: Boolean; Name: String): THandle;
  external 'CreateMutexW@kernel32.dll stdcall';

procedure ReleaseInstallationLock;
begin
  if InstallationLock <> 0 then begin
    CloseHandle(InstallationLock);
    InstallationLock := 0;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then ReleaseInstallationLock;
end;

procedure DeinitializeSetup;
begin
  ReleaseInstallationLock;
end;

function IsSentryUpdate: Boolean;
begin
  Result := ExpandConstant('{param:SENTRYUPDATE|0}') = '1';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ProcessId: Integer;
  MutexError: LongWord;
  Handle: THandle;
begin
  Result := '';
  if IsSentryUpdate then begin
    ProcessId := StrToIntDef(ExpandConstant('{param:UPDATEFROMPID|0}'), 0);
    if ProcessId <= 0 then begin
      Result := 'No se identificó la aplicación que solicitó la actualización.';
      exit;
    end;
    Handle := OpenProcess($00100000, False, ProcessId);
    if Handle <> 0 then begin
      try
        if WaitForSingleObject(Handle, 60000) <> 0 then
          Result := 'Sentry todavía está abierto. Ciérralo e intenta actualizar de nuevo. Tus datos no se han modificado.';
      finally
        CloseHandle(Handle);
      end;
    end else if LastProcessError <> 87 then
      Result := 'No se pudo comprobar si Sentry está cerrado. Cierra Sentry y vuelve a ejecutar el instalador.';
    if Result <> '' then exit;
  end;
  if InstallationLock <> 0 then exit;
  InstallationLock := CreateApplicationMutex(0, False, 'Ecuaconexion.Sentry');
  MutexError := LastProcessError;
  if (InstallationLock = 0) or (MutexError = 183) then begin
    ReleaseInstallationLock;
    Result := 'Sentry está abierto o no se pudo bloquear la instalación. Cierra todas sus ventanas y vuelve a intentarlo.';
  end;
end;
