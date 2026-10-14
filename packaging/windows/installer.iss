#ifndef AppVersion
  #error AppVersion must be supplied to ISCC with /DAppVersion=...
#endif

[Setup]
AppId={{97A6079B-7F70-4F32-A691-12624B86E88C}
AppName=Spec Surgeon
AppVersion={#AppVersion}
AppPublisher=Abraham Borg
AppPublisherURL=https://github.com/Abe-Borg/spec-surgeon
AppSupportURL=https://github.com/Abe-Borg/spec-surgeon/issues
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
UninstallDisplayIcon={app}\SpecSurgeon.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked

[Files]
Source: "*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Spec Surgeon"; Filename: "{app}\SpecSurgeon.exe"; WorkingDir: "{userdocs}"
Name: "{group}\Example edit specs"; Filename: "{app}\examples\specs"
Name: "{group}\Uninstall Spec Surgeon"; Filename: "{uninstallexe}"
Name: "{userdesktop}\Spec Surgeon"; Filename: "{app}\SpecSurgeon.exe"; WorkingDir: "{userdocs}"; Tasks: desktopicon

[Run]
Filename: "{app}\SpecSurgeon.exe"; Description: "Launch Spec Surgeon"; Flags: nowait postinstall skipifsilent
