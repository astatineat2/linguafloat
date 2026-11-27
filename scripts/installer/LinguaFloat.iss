; AppVersion and DistributionBuild are deliberately separate.
#ifndef AppVersion
  #error Build the distribution snapshot first and compile its Build.iss
#endif
#define AppName "LinguaFloat"
#ifndef MenuGroup
  #define MenuGroup "LinguaFloat"
#endif
#ifndef ProductGuid
  #define ProductGuid "7AB27613-3319-49B5-A2EB-796458D9C0F6"
#endif

[Setup]
AppId={{{#ProductGuid}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} v{#AppVersion}
AppPublisher=LinguaFloat
VersionInfoVersion={#AppVersion}.{#DistributionBuild}
VersionInfoDescription=LinguaFloat 中文安装程序
DefaultDirName={localappdata}\Programs\LinguaFloat
DefaultGroupName={#MenuGroup}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DisableWelcomePage=no
DisableProgramGroupPage=yes
DisableDirPage=no
DisableReadyPage=yes
DirExistsWarning=auto
AllowRootDirectory=no
AllowNetworkDrive=no
UsePreviousAppDir=yes
UsePreviousTasks=yes
UninstallDisplayIcon={app}\LinguaFloat.exe
SetupIconFile=app.ico
WizardStyle=modern light
WizardSizePercent=115
WizardImageFile=wizard.bmp
WizardSmallImageFile=wizard-small.bmp
WizardImageStretch=yes
WizardImageBackColor=#F6F6F9
WizardBackColor=#F6F6F9
ShowLanguageDialog=no
LanguageDetectionMethod=none
CloseApplications=no
RestartApplications=no
RestartIfNeededByRun=no
Compression=lzma2/normal
SolidCompression=yes
LZMANumBlockThreads=2
OutputDir={#DistOutput}
OutputBaseFilename=LinguaFloat-v{#AppVersion}-windows-x64-Setup
SetupLogging=yes
Uninstallable=yes
UninstallDisplayName=LinguaFloat v{#AppVersion}
ExtraDiskSpaceRequired=134217728

[Languages]
Name: "zh"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Messages]
WelcomeLabel1=欢迎安装 LinguaFloat
WelcomeLabel2=即将安装 LinguaFloat v{#AppVersion}。%n%n本安装包包含桌面程序和文档运行环境，无需自行安装 Python。%n%n开始前请保存工作。若安装目录中的程序正在运行，请先通过托盘退出。%n%n已有配置、历史与密钥会保留。
WizardSelectDir=选择安装位置
SelectDirDesc=请选择 LinguaFloat 的安装文件夹。
SelectDirLabel3=可使用默认位置，也可输入路径或点击“浏览”选择其他文件夹。
BrowseDialogLabel=请选择磁盘或文件夹；点击“新建文件夹”后可输入名称，名称需要修改时可直接重新编辑。
WizardSelectTasks=快捷方式
SelectTasksDesc=选择需要创建的快捷方式。
SelectTasksLabel2=安装程序会创建开始菜单入口。你还可以选择创建桌面快捷方式。
WizardReady=准备安装
ReadyLabel1=请确认安装位置和快捷方式，然后点击“安装”。
WizardInstalling=正在安装 LinguaFloat
InstallingLabel=正在安装程序和文档组件，请稍候。
FinishedHeadingLabel=LinguaFloat 安装完成
FinishedLabel=LinguaFloat v{#AppVersion} 已安装。
FinishedLabelNoIcons=LinguaFloat v{#AppVersion} 已安装。
ClickFinish=点击“完成”退出安装程序。
ButtonNext=下一步(&N) >
ButtonBack=< 上一步(&B)
ButtonInstall=安装(&I)
ButtonFinish=完成(&F)
ButtonCancel=取消
ButtonBrowse=浏览(&B)...
UninstallAppFullTitle=卸载 LinguaFloat
ConfirmUninstall=是否卸载 LinguaFloat？%n%n仅移除安装程序登记的运行文件和快捷方式，保留用户配置、历史、缓存和系统保存的密钥。

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "可选快捷方式"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\*"; Excludes: "\LinguaFloat.exe,\VERSION.txt"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#PayloadDir}\LinguaFloat.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\VERSION.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\LinguaFloat"; Filename: "{app}\LinguaFloat.exe"; WorkingDir: "{app}"
Name: "{group}\卸载 LinguaFloat"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LinguaFloat"; Filename: "{app}\LinguaFloat.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\LinguaFloat.exe"; Description: "启动 LinguaFloat"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Code]
var
  SetupGate: THandle;

function CreateFileW(Name: String; Access, Share: Cardinal; Security: LongWord;
  Disposition, Attributes: Cardinal; Template: THandle): THandle;
  external 'CreateFileW@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';
function ShowWindow(Handle: HWND; CmdShow: Integer): Boolean;
  external 'ShowWindow@user32.dll stdcall';

procedure ReleaseSetupGate;
begin
  if SetupGate <> 0 then begin
    CloseHandle(SetupGate);
    SetupGate := 0;
  end;
end;

procedure ChooseInstallDirectory(Sender: TObject);
var
  Directory: String;
begin
  Directory := WizardDirValue;
  if BrowseForFolder('选择安装位置，可新建文件夹并修改名称', Directory, True) then
    WizardForm.DirEdit.Text := Directory;
end;

function IsWithin(const Child, Parent: String): Boolean;
begin
  Result := (CompareText(AddBackslash(Child), AddBackslash(Parent)) = 0) or
    (Pos(Lowercase(AddBackslash(Parent)), Lowercase(AddBackslash(Child))) = 1);
end;

function RunningInTarget(const Target: String): String;
var
  Locator, Service, Processes, Process: Variant;
  I: Integer;
  ImagePath: String;
begin
  Result := '';
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Service := Locator.ConnectServer('', 'root\CIMV2');
    Processes := Service.ExecQuery('SELECT Name, ExecutablePath FROM Win32_Process WHERE ' +
      '(Name = "LinguaFloat.exe") OR (Name = "LinguaFloatUpdater.exe") OR ' +
      '(Name = "python.exe") OR (Name = "pythonw.exe")');
    for I := 0 to Processes.Count - 1 do begin
      Process := Processes.ItemIndex(I);
      if not VarIsNull(Process.ExecutablePath) then begin
        ImagePath := Process.ExecutablePath;
        if IsWithin(ExtractFileDir(ImagePath), Target) then begin
          Result := Process.Name;
          Exit;
        end;
      end;
    end;
  except
    Result := '#check-failed';
    Log('Could not verify running processes: ' + GetExceptionMessage);
  end;
end;

function ValidateTarget(const Target: String): String;
var
  ProbeParent, ProbeFile, Registered: String;
  Entry: TFindRec;
begin
  Result := '';
  if IsWithin(Target, ExpandConstant('{win}')) or
     (CompareText(Target, RemoveBackslash(ExtractFileDrive(Target))) = 0) or
     (Length(Target) <= 3) or (Pos('\\', Target) = 1) then begin
    Result := '请选择本地磁盘中的专用安装文件夹，不要选择 Windows 目录或磁盘根目录。';
    Exit;
  end;
  if FileExists(Target) then begin
    Result := '安装位置与已有文件重名，请选择其他文件夹。';
    Exit;
  end;
  if RegQueryStringValue(HKCU64, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{{#ProductGuid}}_is1', 'InstallLocation', Registered) then begin
    if CompareText(RemoveBackslashUnlessRoot(Registered), RemoveBackslashUnlessRoot(Target)) <> 0 then begin
      Result := '当前用户已经在其他位置安装 LinguaFloat。请更新原安装，或先从开始菜单卸载。';
      Exit;
    end;
  end else if FindFirst(AddBackslash(Target) + '*', Entry) then begin
    try
      repeat
        if (Entry.Name <> '.') and (Entry.Name <> '..') then begin
          Result := '首次使用 Setup 请选择空的专用文件夹，以免覆盖已有文件。';
          Exit;
        end;
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
  ProbeParent := Target;
  while not DirExists(ProbeParent) do begin
    if ExtractFileDir(ProbeParent) = ProbeParent then Break;
    ProbeParent := ExtractFileDir(ProbeParent);
  end;
  ProbeFile := AddBackslash(ProbeParent) + '.linguafloat-install-' + GetDateTimeString('yyyymmddhhnnsszzz', #0, #0) + '.tmp';
  if not CreateDir(ProbeFile) then
    Result := '无法写入所选位置，请选择当前用户有写入权限的文件夹。'
  else if not RemoveDir(ProbeFile) then
    Result := '无法清理安装位置的权限检查文件，请选择其他目录并检查磁盘权限。';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Problem: String;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and not WizardSilent then begin
    Problem := ValidateTarget(WizardDirValue);
    if Problem <> '' then begin
      MsgBox(Problem, mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpSelectTasks then
    WizardForm.NextButton.Caption := SetupMessage(msgButtonInstall);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Target, Running, GatePath, Marker, App, Previous: String;
  WasIncomplete, Parked: Boolean;
begin
  Target := WizardDirValue;
  Result := ValidateTarget(Target);
  if Result <> '' then Exit;
  if not DirExists(Target) and not ForceDirectories(Target) then begin
    Result := '无法创建安装目录。';
    Exit;
  end;
  GatePath := AddBackslash(Target) + '.linguafloat-setup.lock';
  SetupGate := CreateFileW(GatePath, $40000000, 0, 0, 4, $80, 0);
  if SetupGate = THandle(-1) then begin
    SetupGate := 0;
    Result := '此目录已有安装正在进行，或无法锁定目录。';
    Exit;
  end;
  Running := RunningInTarget(Target);
  if Running = '#check-failed' then
    Result := '无法核实安装目录中的运行进程。为避免覆盖正在处理的文件，请退出程序后重试；仍有问题时请保留安装日志。'
  else if Running <> '' then
    Result := '安装目录中仍有程序运行：' + Running + '。请先保存任务并通过托盘退出 LinguaFloat，再重试。安装程序不会强行结束任务。';
  if Result <> '' then begin
    ReleaseSetupGate;
    DeleteFile(GatePath);
    Exit;
  end;
  Marker := AddBackslash(Target) + '.linguafloat-setup-incomplete';
  App := AddBackslash(Target) + 'LinguaFloat.exe';
  Previous := AddBackslash(Target) + 'LinguaFloat.update-previous.bak';
  WasIncomplete := FileExists(Marker);
  Parked := False;
  if not SaveStringToFile(Marker, '{#AppVersion}', False) then
    Result := '无法记录安装状态，未替换程序。'
  else if FileExists(App) and not (WasIncomplete and FileExists(Previous)) then begin
    { Copy then delete: Windows shortcuts can follow the old EXE if it is moved. }
    if FileCopy(App, Previous, True) then begin
      Parked := DeleteFile(App);
      if not Parked then DeleteFile(Previous);
    end;
    if not Parked then
      Result := '无法停用旧版启动入口。请从托盘退出 LinguaFloat，再重试。'
    else if RunningInTarget(Target) <> '' then
      Result := '准备安装期间程序被打开，未替换组件。请退出后重试。';
  end;
  if Result <> '' then begin
    if Parked then
      if not RenameFile(Previous, App) then
        WasIncomplete := True;
    if not WasIncomplete then DeleteFile(Marker);
    ReleaseSetupGate;
    DeleteFile(GatePath);
    Exit;
  end;
  if WizardSilent and (CompareText(ExtractFileName(ExpandConstant('{srcexe}')), 'Setup.exe') = 0) then
    ShowWindow(WizardForm.Handle, 4);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Registered: String;
begin
  if CurStep <> ssPostInstall then Exit;
#ifdef HasSetupReceipt
#include "VerifyPayload.iss"
#endif
  if not RegQueryStringValue(HKCU64, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{{#ProductGuid}}_is1', 'DisplayVersion', Registered) or
     (Registered <> '{#AppVersion}') then
    RaiseException('未确认 Windows 安装登记已更新。请用官方 Setup 原地修复。');
  if not DeleteFile(ExpandConstant('{app}\.linguafloat-setup-incomplete')) then
    RaiseException('无法清除安装未完成记录。请用官方 Setup 原地修复。');
  if not WizardSilent then begin
    ReleaseSetupGate;
    DeleteFile(ExpandConstant('{app}\.linguafloat-setup.lock'));
  end;
  DeleteFile(ExpandConstant('{app}\LinguaFloat.update-previous.bak'));
end;

procedure DeinitializeSetup;
begin
  if SetupGate <> 0 then begin
    ReleaseSetupGate;
    DeleteFile(AddBackslash(WizardDirValue) + '.linguafloat-setup.lock');
  end;
end;

function InitializeUninstall(): Boolean;
var
  Running: String;
begin
  Running := RunningInTarget(ExpandConstant('{app}'));
  Result := Running = '';
  if not Result then
    SuppressibleMsgBox('无法安全卸载：请先保存任务并通过托盘退出 LinguaFloat，待文档处理结束后重试。用户配置和历史不会删除。', mbError, MB_OK, IDOK);
end;

procedure InitializeWizard;
begin
  WizardForm.DirBrowseButton.OnClick := @ChooseInstallDirectory;
  WizardForm.Font.Name := 'Microsoft YaHei UI';
  WizardForm.Font.Size := 9;
  WizardForm.Font.Color := $38221C;
  WizardForm.WelcomeLabel1.Font.Color := $D34162;
  WizardForm.WelcomeLabel1.Font.Size := 18;
  WizardForm.FinishedHeadingLabel.Font.Color := $D34162;
  WizardForm.PageNameLabel.Font.Color := $D34162;
  WizardForm.DirEdit.Color := clWhite;
  WizardForm.GroupEdit.Color := clWhite;
  WizardForm.ReadyMemo.Color := clWhite;
  WizardForm.StatusLabel.Font.Color := $38221C;
end;
