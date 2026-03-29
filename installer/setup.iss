; OpenAVC Installer Script for Inno Setup 6
;
; Builds: OpenAVC-Setup-{version}.exe
;
; Install Inno Setup 6 from https://jrsoftware.org/isdl.php (BSD license)
; Build:  "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\setup.iss
;
; Before running this script, build the PyInstaller bundles:
;   pyinstaller installer/openavc.spec --noconfirm
;   pyinstaller installer/tray.spec --noconfirm

#define MyAppName "OpenAVC"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "OpenAVC"
#define MyAppURL "https://openavc.com"
#define MyAppExeName "openavc-server.exe"
#define MyTrayExeName "openavc-tray.exe"

[Setup]
AppId={{B8F3D2A1-7C4E-4F1A-9D8B-2E5A6C3F0D1E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=OpenAVC-Setup-{#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\openavc.ico
SetupIconFile=openavc.ico
; Don't restart the app automatically after uninstall/upgrade
CloseApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Components]
Name: "server"; Description: "OpenAVC Server"; Types: full compact; Flags: fixed
Name: "tray"; Description: "System Tray Application"; Types: full
Name: "service"; Description: "Install as Windows Service (auto-start on boot)"; Types: full
Name: "shortcuts"; Description: "Desktop and Start Menu shortcuts"; Types: full

[Files]
; Server bundle
Source: "..\dist\openavc\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs; Components: server
; Tray app bundle (merge into same directory to share DLLs)
Source: "..\dist\openavc-tray\openavc-tray.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: tray
Source: "..\dist\openavc-tray\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs skipifsourcedoesntexist; Components: tray
; NSSM
Source: "nssm.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: service
; Service scripts
Source: "install-service.bat"; DestDir: "{app}"; Flags: ignoreversion; Components: service
Source: "uninstall-service.bat"; DestDir: "{app}"; Flags: ignoreversion; Components: service
; Icon
Source: "openavc.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Desktop shortcut
Name: "{autodesktop}\OpenAVC Programmer"; Filename: "http://localhost:8080/programmer"; IconFilename: "{app}\openavc.ico"; Components: shortcuts
; Start Menu
Name: "{group}\OpenAVC Programmer"; Filename: "http://localhost:8080/programmer"; IconFilename: "{app}\openavc.ico"; Components: shortcuts
Name: "{group}\OpenAVC Panel"; Filename: "http://localhost:8080/panel"; IconFilename: "{app}\openavc.ico"; Components: shortcuts
Name: "{group}\Uninstall OpenAVC"; Filename: "{uninstallexe}"; Components: shortcuts
; Tray app in Startup folder (use common startup so it works for all users)
Name: "{commonstartup}\OpenAVC Tray"; Filename: "{app}\openavc-tray.exe"; Components: tray

[Run]
; Install and start service
Filename: "{app}\install-service.bat"; Parameters: """{app}"" ""{commonappdata}\OpenAVC"""; Flags: runhidden waituntilterminated; Components: service; StatusMsg: "Installing OpenAVC service..."
; Launch tray app
Filename: "{app}\openavc-tray.exe"; Flags: nowait postinstall skipifsilent; Components: tray; Description: "Launch OpenAVC system tray"
; Open Programmer IDE in browser
Filename: "http://localhost:8080/programmer"; Flags: shellexec nowait postinstall skipifsilent; Description: "Open Programmer IDE in browser"

[UninstallRun]
; Stop and remove service
Filename: "{app}\uninstall-service.bat"; Parameters: """{app}"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"; Components: service
; Kill tray app
Filename: "taskkill.exe"; Parameters: "/F /IM openavc-tray.exe"; Flags: runhidden; RunOnceId: "KillTray"; Components: tray

[Code]
// Add firewall rule during install, remove during uninstall

procedure AddFirewallRule();
var
  ResultCode: Integer;
begin
  Exec('netsh.exe',
    'advfirewall firewall add rule name="OpenAVC" dir=in action=allow protocol=TCP localport=8080 program="' + ExpandConstant('{app}\openavc-server.exe') + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure RemoveFirewallRule();
var
  ResultCode: Integer;
begin
  Exec('netsh.exe',
    'advfirewall firewall delete rule name="OpenAVC"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    AddFirewallRule();
  end;
end;

procedure RemoveAppDirLeftovers();
var
  AppDir: String;
begin
  // Remove empty directories the server may have created at runtime
  // inside the bundle (e.g., _internal/saved_projects/, _internal/projects/default/themes/)
  AppDir := ExpandConstant('{app}');
  DelTree(AppDir + '\_internal', True, False, True);
  RemoveDir(AppDir + '\_internal');
  RemoveDir(AppDir);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    RemoveFirewallRule();
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    RemoveAppDirLeftovers();
  end;
end;

// Seed default project to data directory if not already present
procedure SeedDefaultProject();
var
  DataDir: String;
  SrcDir: String;
begin
  DataDir := ExpandConstant('{commonappdata}\OpenAVC\projects\default');
  SrcDir := ExpandConstant('{app}\_internal\projects\default');
  if not DirExists(DataDir) then
  begin
    ForceDirectories(DataDir);
    // Copy default project
    CopyFile(SrcDir + '\project.avc', DataDir + '\project.avc', False);
  end;
end;
