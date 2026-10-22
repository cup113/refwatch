; refwatch 的 Windows 安装包（Inno Setup 6）。
;
; 两个刻意的边界：
;   * 每用户安装（PrivilegesRequired=lowest）到 %LOCALAPPDATA%\Programs\refwatch —— 不需要管理员，
;     而且与数据目录 %LOCALAPPDATA%\refwatch 分开，升级/卸载都不会碰 state.json。
;   * 卸载默认**保留**数据；只有用户在卸载页明确选「是」才删（state.json 是唯一真相）。
;
; 构建（CI 里由 .github/workflows/build.yml 调）：
;   python -m nuitka --standalone --output-dir=build --output-filename=refwatch.exe tray.py
;   iscc /DAPP_VERSION=v1.2.0 installer.iss

#ifndef APP_VERSION
  #define APP_VERSION "0.0.0-dev"
#endif

#define APP_NAME "refwatch"
#define APP_EXE "refwatch.exe"
#define DATA_DIR "{localappdata}\refwatch"

[Setup]
; AppId 是固定的唯一标识，不能随手改：改了就等于「另一个产品」，升级/卸载都会走错。
AppId={{1F563AE7-E050-4384-B18F-DD75D9A5A14C}
AppName={#APP_NAME}
AppVersion={#APP_VERSION}
AppVerName={#APP_NAME} {#APP_VERSION}
AppPublisher=refwatch
AppPublisherURL=https://github.com/cup113/refwatch
AppSupportURL=https://github.com/cup113/refwatch/issues
DefaultDirName={localappdata}\Programs\{#APP_NAME}
DefaultGroupName={#APP_NAME}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Output
OutputBaseFilename=refwatch-setup-{#APP_VERSION}
SetupIconFile=assets\refwatch.ico
UninstallDisplayIcon={app}\{#APP_EXE}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 托盘进程会占着 exe，安装/升级前先把它停掉（见 [Code]）。
CloseApplications=no
RestartApplications=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"
Name: "autostart"; Description: "登录时自动启动（推荐，托盘常驻会在后台按周期检查）"; GroupDescription: "附加任务:"

[Files]
; Nuitka --standalone 的产物目录（exe + 依赖 + assets/）
Source: "build\tray.dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\refwatch"; Filename: "{app}\{#APP_EXE}"
Name: "{group}\refwatch 数据目录"; Filename: "{localappdata}\refwatch"
Name: "{group}\卸载 refwatch"; Filename: "{uninstallexe}"
Name: "{userdesktop}\refwatch"; Filename: "{app}\{#APP_EXE}"; Tasks: desktopicon

[Registry]
; 自启的注册表投影；uninsdeletevalue 保证卸载时不留孤儿。
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
    ValueName: "refwatch"; ValueData: """{app}\{#APP_EXE}"" --tray"; \
    Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#APP_EXE}"; Description: "立即启动 refwatch（托盘）"; Flags: nowait postinstall skipifsilent
Filename: "{localappdata}\refwatch"; Description: "打开数据目录（配置与日志都在这）"; Flags: postinstall shellexec unchecked

[Code]
{ 安装/升级前先把正在跑的托盘实例停掉：它占着 exe，直接覆盖会失败。 }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM {#APP_EXE} /F', '', SW_HIDE,
       ewWaitUntilTerminated, ResultCode);
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  DataDir := ExpandConstant('{#DATA_DIR}');
  if not DirExists(DataDir) then
    Exit;
  { 默认是「否」：state.json 里是每个 ref 上一轮的 SHA，删掉等于让所有仓库重新静默建基线。 }
  if MsgBox('是否同时删除配置、状态与日志？' + #13#10 + #13#10 + DataDir + #13#10 + #13#10 +
            '选择「否」会保留它们，以后重装可以直接接着用。',
            mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
    DelTree(DataDir, True, True, True);
end;
