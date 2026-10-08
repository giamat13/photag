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
ChangesAssociations=yes
UninstallDisplayName=photag
UninstallDisplayIcon={app}\photag.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "openwith"; Description: "Add photag to the Open with menu of pictures (shows a picture without adding it to the library)"; GroupDescription: "Pictures:"

[Files]
Source: "dist\photag\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "dist\photag-backup.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; an update replaces the program's own files completely (no stale libraries from older versions); data lives elsewhere
Type: filesandordirs; Name: "{app}\_internal"
; a full installer carries the newest code itself: code updates (code, code.prev ...) from earlier versions must not override it
Type: filesandordirs; Name: "{app}\code"
Type: filesandordirs; Name: "{app}\code.prev"
Type: filesandordirs; Name: "{app}\code.new"
Type: filesandordirs; Name: "{app}\code.bad"

[Icons]
Name: "{autoprograms}\photag"; Filename: "{app}\photag.exe"
Name: "{autodesktop}\photag"; Filename: "{app}\photag.exe"; Tasks: desktopicon

[Registry]
; same keys as app/fileassoc.py (current user only); removed again on uninstall
Root: HKCU; Subkey: "Software\Classes\photag.Image"; ValueType: string; ValueData: "Picture"; Flags: uninsdeletekey; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\photag.Image\DefaultIcon"; ValueType: string; ValueData: """{app}\photag.exe"",0"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\photag.Image\shell\open"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "photag"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\photag.Image\shell\open\command"; ValueType: string; ValueData: """{app}\photag.exe"" ""%1"""; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "photag"; Flags: uninsdeletekey; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\shell\open\command"; ValueType: string; ValueData: """{app}\photag.exe"" ""%1"""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "photag"; Flags: uninsdeletekey; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Photo manager and picture viewer: shows a picture without adding it to the catalog"; Tasks: openwith
Root: HKCU; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "photag"; ValueData: "Software\photag\Capabilities"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.jpg\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".jpg"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".jpg"; ValueData: "photag.Image"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.jpeg\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".jpeg"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".jpeg"; ValueData: "photag.Image"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.jpe\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".jpe"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".jpe"; ValueData: "photag.Image"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.jfif\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".jfif"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".jfif"; ValueData: "photag.Image"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.png\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".png"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".png"; ValueData: "photag.Image"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.gif\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".gif"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".gif"; ValueData: "photag.Image"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.webp\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".webp"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".webp"; ValueData: "photag.Image"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.bmp\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".bmp"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".bmp"; ValueData: "photag.Image"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.tif\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".tif"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".tif"; ValueData: "photag.Image"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.tiff\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".tiff"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".tiff"; ValueData: "photag.Image"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.heic\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".heic"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".heic"; ValueData: "photag.Image"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.heif\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".heif"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".heif"; ValueData: "photag.Image"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.avif\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".avif"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".avif"; ValueData: "photag.Image"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.dng\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".dng"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".dng"; ValueData: "photag.Image"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.cr2\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".cr2"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".cr2"; ValueData: "photag.Image"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.cr3\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".cr3"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".cr3"; ValueData: "photag.Image"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.nef\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".nef"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".nef"; ValueData: "photag.Image"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.arw\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".arw"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".arw"; ValueData: "photag.Image"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.orf\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".orf"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".orf"; ValueData: "photag.Image"; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\.rw2\OpenWithProgids"; ValueType: none; ValueName: "photag.Image"; Flags: uninsdeletevalue; Tasks: openwith
Root: HKCU; Subkey: "Software\Classes\Applications\photag.exe\SupportedTypes"; ValueType: string; ValueName: ".rw2"; ValueData: ""; Tasks: openwith
Root: HKCU; Subkey: "Software\photag\Capabilities\FileAssociations"; ValueType: string; ValueName: ".rw2"; ValueData: "photag.Image"; Tasks: openwith

[UninstallRun]
; the app's backup task (created by the app itself) goes away with it; photos and backups are NOT touched
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""photag-backup"" /F"; Flags: runhidden; RunOnceId: "DelBackupTask"
Filename: "reg.exe"; Parameters: "delete ""HKCU\Software\Microsoft\Windows\CurrentVersion\Run"" /v photag-backup /f"; Flags: runhidden; RunOnceId: "DelBackupRunKey"

[Run]
Filename: "{app}\photag.exe"; Description: "Launch photag"; Flags: nowait postinstall skipifsilent
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
