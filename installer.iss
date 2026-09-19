; Inno Setup script -> installer_output\PhotoManagerSetup.exe
; Build:  ISCC.exe installer.iss   (after: pyinstaller photo_manager.spec)
; Per-user install (no admin). Photos/catalog live in %USERPROFILE%\PhotoManager and
; are NOT touched by install or uninstall.

[Setup]
AppId={{6F0B7C1E-3A52-4D8B-9C47-5E2A1D0F8B36}
AppName=PhotoManager
AppVersion=1.0
AppPublisher=PhotoManager
DefaultDirName={autopf}\PhotoManager
DefaultGroupName=PhotoManager
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=installer_output
OutputBaseFilename=PhotoManagerSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=PhotoManager
UninstallDisplayIcon={app}\PhotoManager.exe

[Tasks]
Name: "desktopicon"; Description: "צור קיצור דרך בשולחן העבודה"; GroupDescription: "קיצורי דרך:"

[Files]
Source: "dist\PhotoManager.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\PhotoManager"; Filename: "{app}\PhotoManager.exe"
Name: "{autodesktop}\PhotoManager"; Filename: "{app}\PhotoManager.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PhotoManager.exe"; Description: "הפעל את PhotoManager"; Flags: nowait postinstall skipifsilent
