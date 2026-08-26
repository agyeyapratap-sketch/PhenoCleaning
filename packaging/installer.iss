; Inno Setup script — wraps the PyInstaller one-folder build into a Windows installer.
; Build the app first (pyinstaller packaging\phenocleaning.spec), then compile this with Inno Setup:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\installer.iss
; Output: packaging\Output\PhenoCleaning-Setup.exe  (a double-click installer for non-coders).
; The CI workflow (.github/workflows/build-app.yml) compiles this automatically on Windows.

#define MyAppName "PhenoCleaning"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Agyeya Pratap"
#define MyAppExeName "PhenoCleaning.exe"
; The PyInstaller one-folder output to package (repo-root\dist\PhenoCleaning).
; Override on the command line if you built elsewhere:  ISCC ... /DSourceDir="C:\path\to\PhenoCleaning"
#ifndef SourceDir
  #define SourceDir "..\dist\PhenoCleaning"
#endif

[Setup]
AppId={{7C3E1B94-2A5D-4F8E-9B1C-6D0A4E2F7A31}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\PhenoCleaning
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=Output
OutputBaseFilename=PhenoCleaning-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Per-user install: no administrator rights needed (ideal on managed/university machines).
PrivilegesRequired=lowest
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoProductName={#MyAppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent
