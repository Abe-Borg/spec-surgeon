#ifndef AppVersion
  #error AppVersion must be supplied to ISCC with /DAppVersion=...
#endif

; Names shared with the app (tests/test_packaging.py pins them):
;   OutputBaseFilename  == spec_surgeon.updater.INSTALLER_NAME
;   AppMutexName        == spec_surgeon.winapp.APP_MUTEX
;   AppUserModelID      == spec_surgeon.winapp.AUMID
;   AppCopyright, AppContact, LicenseFile and the links on the welcome page == spec_surgeon.about
;     (COPYRIGHT, EMAIL, the LICENSE file at the repository root, GITHUB_URL, LINKEDIN_URL)

[Setup]
AppId={{97A6079B-7F70-4F32-A691-12624B86E88C}
AppName=Spec Surgeon
AppVersion={#AppVersion}
AppVerName=Spec Surgeon {#AppVersion}
AppPublisher=Abraham Borg
AppPublisherURL=https://github.com/Abe-Borg/spec-surgeon
AppSupportURL=https://github.com/Abe-Borg/spec-surgeon
AppUpdatesURL=https://github.com/Abe-Borg/spec-surgeon/releases
AppContact=abrahamborg@gmail.com
AppCopyright=Copyright (c) 2026 Abraham Borg
; The MIT License (the LICENSE file at the repository root); the wizard shows it and asks for agreement.
LicenseFile={#SourcePath}..\..\LICENSE
; The welcome page carries the license summary and the author's GitHub and LinkedIn links (InitializeWizard).
DisableWelcomePage=no
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

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nSpec Surgeon is free, open-source software by Abraham Borg, released under the MIT License: use it, change it and share it as you like, keep the copyright notice with it, and note that it comes as is, without warranty. The full text is on the next page.%n%nIt is recommended that you close all other applications before continuing.
BeveledLabel=Spec Surgeon  -  Copyright (c) 2026 Abraham Borg  -  MIT License

[Files]
Source: "*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourcePath}..\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

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
  GitHubUrl = 'https://github.com/Abe-Borg/spec-surgeon';
  LinkedInUrl = 'https://www.linkedin.com/in/abrahamborg/';

function ShouldRelaunch(): Boolean;
begin
  Result := WizardSilent() and (ExpandConstant('{param:RELAUNCH|0}') = '1');
end;

procedure OpenUrl(const Url: String);
var
  ErrorCode: Integer;
begin
  ShellExec('open', Url, '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

procedure LinkClick(Sender: TObject);
begin
  OpenUrl(TNewStaticText(Sender).Hint);
end;

function AddLink(const Caption, Url: String; Top: Integer): TNewStaticText;
begin
  Result := TNewStaticText.Create(WizardForm);
  Result.Parent := WizardForm.WelcomePage;
  Result.Left := WizardForm.WelcomeLabel2.Left;
  Result.Top := Top;
  Result.Width := WizardForm.WelcomeLabel2.Width;
  Result.Caption := Caption;
  Result.Hint := Url;
  Result.Cursor := crHand;
  Result.Font.Color := clBlue;
  Result.Font.Style := [fsUnderline];
  Result.OnClick := @LinkClick;
end;

procedure InitializeWizard();
var
  Top: Integer;
begin
  { The author's links under the welcome text; a click opens the page in the default browser. }
  Top := WizardForm.WelcomeLabel2.Top + WizardForm.WelcomeLabel2.Height + ScaleY(10);
  AddLink('GitHub: github.com/Abe-Borg/spec-surgeon', GitHubUrl, Top);
  AddLink('LinkedIn: linkedin.com/in/abrahamborg', LinkedInUrl, Top + ScaleY(18));
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
