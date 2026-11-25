; Installateur Windows de WiFi Audit Pro (Inno Setup 6)
; Compilation : build.ps1 (ou ISCC.exe installer\WiFiAuditPro.iss)

#define AppName "WiFi Audit Pro"
#define AppVersion "1.0.4"
#define AppExe "WiFiAuditPro.exe"

[Setup]
AppId={{3D2A71C4-9E5B-4F08-B1A6-7C0E52D84F13}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=SCHMOCK
AppPublisherURL=https://schmock.fr
AppSupportURL=https://schmock.fr
AppCopyright=© 2026 SCHMOCK
DefaultDirName={autopf}\WiFi Audit Pro
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Installation pour l'utilisateur courant par défaut (pas de droits admin requis)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=WiFiAuditPro-Setup-{#AppVersion}
SetupIconFile=..\assets\WiFiAuditPro.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer une icône sur le Bureau"; GroupDescription: "Raccourcis :"
Name: "autostart"; Description: "Lancer WiFi Audit Pro au démarrage de Windows"; GroupDescription: "Options :"; Flags: unchecked

[Files]
Source: "..\build\app\WiFiAuditPro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Documentation"; Filename: "{app}\README.md"
Name: "{group}\Désinstaller {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "WiFiAuditPro"; ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "Lancer {#AppName}"; Flags: nowait postinstall skipifsilent

