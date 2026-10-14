#ifndef AppVersion
  #error AppVersion must be supplied to ISCC with /DAppVersion=...
#endif

; Names shared with the app (tests/test_packaging.py pins them):
;   OutputBaseFilename  == spec_surgeon.updater.INSTALLER_NAME
;   AppMutexName        == spec_surgeon.winapp.APP_MUTEX
;   AppUserModelID      == spec_surgeon.winapp.AUMID

[Setup]
AppId={{97A6079B-7F70-4F32-A691-12624B86E88C}
AppName=Spec Surgeon
AppVersion={#AppVersion}
AppVerName=Spec Surgeon {#AppVersion}
AppPublisher=Abraham Borg
AppPublisherURL=https://github.com/Abe-Borg/spec-surgeon
AppSupportURL=https://github.com/Abe-Borg/spec-surgeon/issues
AppUpdatesURL=https://github.com/Abe-Borg/spec-surgeon/releases
DefaultDirName={localappdata}\Programs\Spec Surgeon
DefaultGroupName=Spec Surgeon
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
SourceDir=..\..\dist\SpecSurgeon
OutputDir=..\installer
OutputBaseFilename=SpecSurgeon-{#AppVersion}-Setup-x64
SetupIconFile={#SourcePath}SpecSurgeon.ico
UninstallDisplayIcon={app}\SpecSurgeon.exe
UninstallDisplayName=Spec Surgeon
VersionInfoVersion={#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; An interactive install closes a running Spec Surgeon through Windows' Restart Manager. An in-app update
; (/SILENT ... /RELAUNCH=1) instead waits for the app to exit — see InitializeSetup below.
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[InstallDelete]
; A fresh runtime on every install and update: files an older version shipped (an older Qt or Python, the Tk
; runtime of 0.3.x) must not linger, since Qt loads every plugin it finds in its folders.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Spec Surgeon"; Filename: "{app}\SpecSurgeon.exe"; WorkingDir: "{userdocs}"; AppUserModelID: "AbrahamBorg.SpecSurgeon"
Name: "{group}\Example edit specs"; Filename: "{app}\examples\specs"
Name: "{group}\Uninstall Spec Surgeon"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Spec Surgeon"; Filename: "{app}\SpecSurgeon.exe"; WorkingDir: "{userdocs}"; AppUserModelID: "AbrahamBorg.SpecSurgeon"; Tasks: desktopicon

[Run]
Filename: "{app}\SpecSurgeon.exe"; Description: "Launch Spec Surgeon"; Flags: nowait postinstall skipifsilent
; In-app update: the app started Setup with /SILENT /RELAUNCH=1 and quit — reopen it when the files are in place.
Filename: "{app}\SpecSurgeon.exe"; WorkingDir: "{userdocs}"; Flags: nowait; Check: ShouldRelaunch

[UninstallDelete]
; Downloaded installers and install logs (settings and results are the user's and stay).
Type: filesandordirs; Name: "{localappdata}\Spec Surgeon\updates"

[Code]
const
  AppMutexName = 'SpecSurgeon-97A6079B-7F70-4F32-A691-12624B86E88C';
  WaitForAppMs = 60000;

function ShouldRelaunch(): Boolean;
begin
  Result := WizardSilent() and (ExpandConstant('{param:RELAUNCH|0}') = '1');
end;

function InitializeSetup(): Boolean;
var
  Waited: Integer;
begin
  Result := True;
  { In-app update: the window that started this Setup is closing. Wait for it (and any spec-surgeon.exe command
    still running a batch) to let go of the app mutex, so no file is replaced while it is in use. }
  if WizardSilent() then
  begin
    Waited := 0;
    while CheckForMutexes(AppMutexName) and (Waited < WaitForAppMs) do
    begin
      Sleep(250);
      Waited := Waited + 250;
    end;
    if CheckForMutexes(AppMutexName) then
    begin
      Log('Spec Surgeon is still running after 60 seconds; the update was not installed.');
      Result := False;
    end;
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := not CheckForMutexes(AppMutexName);
  if (not Result) and (not UninstallSilent()) then
    MsgBox('Close Spec Surgeon (every window and command) before uninstalling it.', mbError, MB_OK);
end;
