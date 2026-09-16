#define MyAppName "Sentry"
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0-beta.1"
#endif
#ifndef MyAppFileVersion
  #define MyAppFileVersion "0.1.0.1"
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
Name: "{autoprograms}\Sentry"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Sentry"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir Sentry"; Flags: nowait postinstall skipifsilent
