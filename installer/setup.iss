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
; MyAppVersion is normally injected by build.bat (or CI) via ISCC /DMyAppVersion=...
; The fallback below only takes effect if neither path supplies it, in which
; case the resulting installer's filename intentionally screams that the
; version didn't propagate.
#ifndef MyAppVersion
#define MyAppVersion "0.0.0-dev"
#endif
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
Name: "shortcuts"; Description: "Desktop and Start Menu shortcuts"; Types: full
; The Windows service is mandatory (it runs the server at boot, and the tray's
; Start/Stop/Restart controls drive it). It is not an optional component on
; purpose: an install without the service leaves the tray unable to start
; anything. Its files and setup/removal steps below are therefore ungated.

[InstallDelete]
; 0.24.1 and earlier laid the bundled data out as _internal\server,
; _internal\simulator, _internal\web and _internal\themes; 0.25.0 puts all of
; it under _internal\openavc. Inno copies over what is there and never removes
; anything, so without this the old set survives the upgrade -- and _internal
; is on the frozen app's sys.path, so a driver's _sim.py that still says
; `from simulator.…` would import the stale 0.24.1 copy instead of failing
; with the message that tells the user to update the driver.
Type: filesandordirs; Name: "{app}\_internal\server"
Type: filesandordirs; Name: "{app}\_internal\simulator"
Type: filesandordirs; Name: "{app}\_internal\web"
Type: filesandordirs; Name: "{app}\_internal\themes"

[Files]
; Server bundle - always installed unconditionally (server is a fixed/required
; component, and Inno Setup silently skips Components:-gated entries on upgrades
; when the registry Inno_SelectedComponents value is empty).
Source: "..\dist\openavc\openavc-server.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\openavc\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs
; Tray app bundle (merge into same directory to share DLLs)
Source: "..\dist\openavc-tray\openavc-tray.exe"; DestDir: "{app}"; Flags: ignoreversion; Components: tray
Source: "..\dist\openavc-tray\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs skipifsourcedoesntexist; Components: tray
; NSSM + service scripts - always installed (the service is mandatory, and like
; the server bundle above, Components:-gating these would let Inno silently skip
; them on silent upgrades when the registry Inno_SelectedComponents value is empty).
Source: "nssm.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "install-service.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "start-service.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "secure-data-dir.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "uninstall-service.bat"; DestDir: "{app}"; Flags: ignoreversion
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
; Install the service, put back the variables an administrator set on the
; previous one, then start it (mandatory)
Filename: "{app}\install-service.bat"; Parameters: """{app}"" ""{commonappdata}\OpenAVC"""; Flags: runhidden waituntilterminated; StatusMsg: "Installing OpenAVC service..."; BeforeInstall: RememberServiceEnvironment; AfterInstall: KeepAdministratorEnvironment
Filename: "{app}\start-service.bat"; Parameters: """{app}"""; Flags: runhidden waituntilterminated; StatusMsg: "Starting OpenAVC service..."
; Launch tray app
Filename: "{app}\openavc-tray.exe"; Flags: nowait postinstall skipifsilent; Components: tray; Description: "Launch OpenAVC system tray"
; Open Programmer IDE in browser
Filename: "http://localhost:8080/programmer"; Flags: shellexec nowait postinstall skipifsilent; Description: "Open Programmer IDE in browser"

[UninstallRun]
; Stop and remove service
Filename: "{app}\uninstall-service.bat"; Parameters: """{app}"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"
; Kill tray app
Filename: "taskkill.exe"; Parameters: "/F /IM openavc-tray.exe"; Flags: runhidden; RunOnceId: "KillTray"; Components: tray

[Code]
// Stop the NSSM service and kill processes BEFORE file copy.
// Without this, openavc-server.exe is locked and Inno Setup silently
// skips it during /VERYSILENT upgrades — the update "succeeds" but
// the old binary remains.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  NssmPath: String;
begin
  Result := '';
  NeedsRestart := False;
  NssmPath := ExpandConstant('{app}\nssm.exe');
  if FileExists(NssmPath) then
    Exec(NssmPath, 'stop OpenAVC', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/F /IM openavc-server.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/F /IM openavc-tray.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

// The firewall's own rule store: one string value per rule, shaped
// "v2.33|Action=Allow|Active=TRUE|Dir=In|App=...|Name=OpenAVC|". Its field
// names are the same in every language; netsh's listing is printed in the
// language Windows is installed in, so what is there is always read from here.
const
  FirewallRulesKey = 'SYSTEM\CurrentControlSet\Services\SharedAccess\Parameters\FirewallPolicy\FirewallRules';
  PluginRuleMarker = '|Name=OpenAVC plugin ';
  NoInboundRule = 0;
  ProgramRule = 1;
  PortRule = 2;

// Add the firewall rule during install, keep it on update, remove it during
// uninstall.
//
// The rule is program-scoped with NO port list: inbound traffic is allowed
// only while openavc-server.exe itself is listening on a port. That way
// every port the server can be configured to use — HTTP (8080 or custom),
// HTTPS (8443 or custom), the optional port-80 Short URLs listener — works
// the moment the feature is enabled in Settings, with no firewall edits and
// nothing left open that the server isn't serving. A port a plugin's own
// helper program listens on is not covered: that is a different executable,
// so the server opens it as a rule of its own (RemovePluginFirewallRules).
//
// An update keeps the rule it finds and only points it at this program, so
// what an administrator set on it (the addresses it allows, its profiles,
// whether it is enabled) stays. The one exception is a rule under this name
// with a port list: installers before 0.23.0 made it TCP 8080 only, which
// would block HTTPS and every other port the server can use, so it is
// replaced. With no rule, one is added that allows every address.

// What the store holds under one inbound rule name.
function InboundRuleKind(const RuleName: String): Integer;
var
  ValueNames: TArrayOfString;
  RuleString: String;
  I: Integer;
begin
  Result := NoInboundRule;
  if not RegGetValueNames(HKLM, FirewallRulesKey, ValueNames) then
    Exit;
  for I := 0 to GetArrayLength(ValueNames) - 1 do
  begin
    if RegQueryStringValue(HKLM, FirewallRulesKey, ValueNames[I], RuleString) and
      (Pos('|Name=' + RuleName + '|', RuleString) > 0) and
      (Pos('|Dir=In|', RuleString) > 0) then
    begin
      if Pos('|LPort', RuleString) > 0 then
      begin
        Result := PortRule;
        Exit;
      end;
      Result := ProgramRule;
    end;
  end;
end;

procedure KeepOrAddProgramRule(const RuleName, ProgramPath: String);
var
  ResultCode: Integer;
  Kind: Integer;
begin
  Kind := InboundRuleKind(RuleName);
  if Kind = ProgramRule then
  begin
    // "set ... new" changes only the fields it names.
    Exec('netsh.exe',
      'advfirewall firewall set rule name="' + RuleName + '" dir=in new program="' + ProgramPath + '"',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    Exit;
  end;
  if Kind = PortRule then
    Exec('netsh.exe',
      'advfirewall firewall delete rule name="' + RuleName + '" dir=in',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('netsh.exe',
    'advfirewall firewall add rule name="' + RuleName + '" dir=in action=allow program="' + ProgramPath + '"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure AddFirewallRule();
begin
  KeepOrAddProgramRule('OpenAVC', ExpandConstant('{app}\openavc-server.exe'));
end;

procedure RemoveFirewallRule();
var
  ResultCode: Integer;
begin
  Exec('netsh.exe',
    'advfirewall firewall delete rule name="OpenAVC"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

// The server opens a port a plugin asks for as a rule of its own, named
// "OpenAVC plugin <PROTOCOL> <port>" (openavc/system/firewall.py). Those rules
// are scoped by port, not by program, so one left behind keeps that port open
// for any program on this computer. They are found in the rule store above.
// Deleting by name removes every rule that carries it, so a name found twice
// is harmless.

procedure RemovePluginFirewallRules();
var
  ValueNames: TArrayOfString;
  RuleString, RuleName: String;
  I, NameStart, NameEnd, ResultCode: Integer;
begin
  if not RegGetValueNames(HKLM, FirewallRulesKey, ValueNames) then
    Exit;
  for I := 0 to GetArrayLength(ValueNames) - 1 do
  begin
    if RegQueryStringValue(HKLM, FirewallRulesKey, ValueNames[I], RuleString) then
    begin
      NameStart := Pos(PluginRuleMarker, RuleString);
      if NameStart > 0 then
      begin
        RuleName := Copy(RuleString, NameStart + Length('|Name='), Length(RuleString));
        NameEnd := Pos('|', RuleName);
        if NameEnd > 0 then
          RuleName := Copy(RuleName, 1, NameEnd - 1);
        Exec('netsh.exe',
          'advfirewall firewall delete rule name="' + RuleName + '"',
          '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      end;
    end;
  end;
end;

// The service's environment.
//
// install-service.bat removes and re-creates the service at every install and
// update, with the installer's environment. Four of its variables are the
// installer's, always: the data, log and project paths and
// OPENAVC_SERVICE_MANAGED (NSSM restarts the server, so it must not start a
// replacement itself). Every other variable on the service is the
// administrator's, and an update keeps it: a proxy, a TLS setting,
// OPENAVC_BIND set to the AV adapter's address. OPENAVC_ALLOW_ANONYMOUS gets
// the installer's value (false) only when the service has none. An installed
// server asks for the admin password with it unset as well, so keeping an
// administrator's value opens nothing they did not open themselves.
//
// The one exception is OPENAVC_BIND=0.0.0.0, which every installer before
// this one set on the service. It is dropped, not kept: it holds Settings >
// Network > Accept connections from at every network, the setting's own
// default on an installed system, and leaves it locked. Any other
// OPENAVC_BIND value was set by someone on purpose, so it stays and keeps
// holding that setting.
//
// The previous service's environment is read before the batch file removes
// it, and the merged one is written before start-service.bat starts the new
// one, so the server never starts on the installer's values in place of the
// administrator's. The merge is done here because a value can hold any
// character (OPENAVC_PROGRAMMER_PASSWORD) and the registry holds it exactly;
// a batch file mangles % ! and ", and nssm's own listing is UTF-16.
const
  ServiceParametersKey = 'SYSTEM\CurrentControlSet\Services\OpenAVC\Parameters';
  ServiceEnvironmentValue = 'AppEnvironmentExtra';
  EarlierInstallerBind = '0.0.0.0';

var
  PreviousServiceEnvironment: String;

// The variable's name, upper-cased (Windows names are case-insensitive), or
// '' for an entry that is not NAME=value.
function EnvironmentName(const Entry: String): String;
var
  Equals: Integer;
begin
  Result := '';
  Equals := Pos('=', Entry);
  if Equals > 1 then
    Result := Uppercase(Copy(Entry, 1, Equals - 1));
end;

// Keep in step with the variables install-service.bat sets.
function IsInstallerVariable(const Name: String): Boolean;
begin
  Result := (Name = 'OPENAVC_DATA_DIR') or (Name = 'OPENAVC_LOG_DIR') or
    (Name = 'OPENAVC_PROJECT') or (Name = 'OPENAVC_SERVICE_MANAGED');
end;

// OPENAVC_BIND as earlier installers set it (see above).
function IsEarlierInstallerBind(const Entry: String): Boolean;
begin
  Result := (EnvironmentName(Entry) = 'OPENAVC_BIND') and
    (Copy(Entry, Pos('=', Entry) + 1, Length(Entry)) = EarlierInstallerBind);
end;

// A REG_MULTI_SZ value as read: entries separated by #0.
procedure SplitEnvironment(const Joined: String; var Entries: TArrayOfString);
var
  Rest: String;
  Separator: Integer;
  Entry: String;
begin
  SetArrayLength(Entries, 0);
  Rest := Joined;
  while Rest <> '' do
  begin
    Separator := Pos(#0, Rest);
    if Separator = 0 then
    begin
      Entry := Rest;
      Rest := '';
    end
    else
    begin
      Entry := Copy(Rest, 1, Separator - 1);
      Rest := Copy(Rest, Separator + 1, Length(Rest));
    end;
    if Entry <> '' then
    begin
      SetArrayLength(Entries, GetArrayLength(Entries) + 1);
      Entries[GetArrayLength(Entries) - 1] := Entry;
    end;
  end;
end;

// The installer's fresh environment, with every administrator's variable from
// the previous one in place of the installer's default or added after it.
function MergeServiceEnvironment(const Previous, Fresh: String): String;
var
  Kept, Merged: TArrayOfString;
  I, J: Integer;
  Name: String;
  Replaced: Boolean;
begin
  SplitEnvironment(Fresh, Merged);
  SplitEnvironment(Previous, Kept);
  for I := 0 to GetArrayLength(Kept) - 1 do
  begin
    Name := EnvironmentName(Kept[I]);
    if (Name <> '') and not IsInstallerVariable(Name) and
      not IsEarlierInstallerBind(Kept[I]) then
    begin
      Replaced := False;
      for J := 0 to GetArrayLength(Merged) - 1 do
      begin
        if EnvironmentName(Merged[J]) = Name then
        begin
          Merged[J] := Kept[I];
          Replaced := True;
        end;
      end;
      if not Replaced then
      begin
        SetArrayLength(Merged, GetArrayLength(Merged) + 1);
        Merged[GetArrayLength(Merged) - 1] := Kept[I];
      end;
    end;
  end;
  Result := '';
  for I := 0 to GetArrayLength(Merged) - 1 do
  begin
    if I > 0 then
      Result := Result + #0;
    Result := Result + Merged[I];
  end;
end;

procedure RememberServiceEnvironment();
begin
  if not RegQueryMultiStringValue(HKLM, ServiceParametersKey, ServiceEnvironmentValue,
    PreviousServiceEnvironment) then
    PreviousServiceEnvironment := '';
end;

procedure KeepAdministratorEnvironment();
var
  Fresh: String;
begin
  if PreviousServiceEnvironment = '' then
    Exit;
  if not RegQueryMultiStringValue(HKLM, ServiceParametersKey, ServiceEnvironmentValue, Fresh) then
    Exit;
  RegWriteMultiStringValue(HKLM, ServiceParametersKey, ServiceEnvironmentValue,
    MergeServiceEnvironment(PreviousServiceEnvironment, Fresh));
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

// Cache the running installer for future rollback.
// Without this, the first update has no cached installer to roll back to.
procedure CacheInstallerForRollback();
var
  CacheDir: String;
  VersionedName: String;
begin
  CacheDir := ExpandConstant('{commonappdata}\OpenAVC\update-cache');
  VersionedName := 'OpenAVC-Setup-{#MyAppVersion}.exe';
  if not DirExists(CacheDir) then
    ForceDirectories(CacheDir);
  if not FileExists(CacheDir + '\' + VersionedName) then
    FileCopy(ExpandConstant('{srcexe}'), CacheDir + '\' + VersionedName, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    AddFirewallRule();
    SeedDefaultProject();
    CacheInstallerForRollback();
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
    RemovePluginFirewallRules();
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    RemoveAppDirLeftovers();
  end;
end;
