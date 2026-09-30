; Inno Setup script -> installer_output\photagSetup.exe
; Build:  ISCC.exe installer.iss   (after: pyinstaller photag.spec)
; Per-user install (no admin). Photos/catalog live in %USERPROFILE%\photag (or the older
; %USERPROFILE%\PhotoManager from before the rename, which keeps being used) and
; are NOT touched by install or uninstall.

[Setup]
AppId={{6F0B7C1E-3A52-4D8B-9C47-5E2A1D0F8B36}
AppName=photag
AppVersion=1.0
AppPublisher=photag
DefaultDirName={autopf}\photag
DefaultGroupName=photag
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer_output
OutputBaseFilename=photagSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=photag
UninstallDisplayIcon={app}\photag.exe

[Tasks]
Name: "desktopicon"; Description: "צור קיצור דרך בשולחן העבודה"; GroupDescription: "קיצורי דרך:"

[Files]
Source: "dist\photag.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\photag"; Filename: "{app}\photag.exe"
Name: "{autodesktop}\photag"; Filename: "{app}\photag.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\photag.exe"; Description: "הפעל את photag"; Flags: nowait postinstall skipifsilent
