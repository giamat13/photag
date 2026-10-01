; Inno Setup script -> installer_output\photagSetup.exe
; Build:  ISCC.exe installer.iss   (after: pyinstaller photag.spec)
; Updates install OVER the existing app (never uninstall-then-install), so an update that is interrupted
; leaves the previous program in place; see app\updater.py ("Interrupted updates").
; Per-user install (no admin). Photos/catalog live in %USERPROFILE%\photag (or the older
; %USERPROFILE%\PhotoManager from before the rename, which keeps being used) and
; are NOT touched by install or uninstall.

; the version comes from app\version.py (build.bat passes /DMyAppVersion=...)
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{6F0B7C1E-3A52-4D8B-9C47-5E2A1D0F8B36}
AppName=photag
AppVersion={#MyAppVersion}
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
SetupIconFile=app\ui\icon.ico
LicenseFile=LICENSE
UninstallDisplayName=photag
UninstallDisplayIcon={app}\photag.exe

[Tasks]
Name: "desktopicon"; Description: "צור קיצור דרך בשולחן העבודה"; GroupDescription: "קיצורי דרך:"

[Files]
Source: "dist\photag.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\photag"; Filename: "{app}\photag.exe"
Name: "{autodesktop}\photag"; Filename: "{app}\photag.exe"; Tasks: desktopicon

[UninstallRun]
; the app's backup task (created by the app itself) goes away with it; photos and backups are NOT touched
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""photag-backup"" /F"; Flags: runhidden; RunOnceId: "DelBackupTask"

[Run]
Filename: "{app}\photag.exe"; Description: "הפעל את photag"; Flags: nowait postinstall skipifsilent
; started by the in-app updater (photagSetup.exe /SILENT /update=1): relaunch the app when the update is done
Filename: "{app}\photag.exe"; Flags: nowait; Check: IsUpdateRun

[Code]
function IsUpdateRun: Boolean;
begin
  Result := ExpandConstant('{param:update|0}') = '1';
end;

// The in-app updater saves a copy of the running program before it starts this installer. When all files are in place
// we write done.flag; if it is missing afterwards (power loss, killed installer) the app puts the saved copy back.
procedure CurStepChanged(CurStep: TSetupStep);
var
  D: String;
begin
  if CurStep = ssPostInstall then
  begin
    D := ExpandConstant('{localappdata}\photag\update');
    ForceDirectories(D);
    SaveStringToFile(D + '\done.flag', '{#MyAppVersion}', False);
  end;
end;
