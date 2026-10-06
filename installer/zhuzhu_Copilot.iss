; ╔══════════════════════════════════════════════════════════╗
; ║  zhuzhu Copilot - Inno Setup 7 安装脚本                 ║
; ║  前置：先运行 build 生成 dist\zhuzhu Copilot（已签名）    ║
; ║  构建入口：build_release.ps1（它负责用 -s 注册 SignTool，  ║
; ║            使安装包与卸载器都在编译期完成签名）            ║
; ║  卸载：使用 Inno 自带卸载器（unins000.exe），卸载时        ║
; ║        结束主程序进程并清除用户数据目录                    ║
; ╚══════════════════════════════════════════════════════════╝

#define MyAppName "zhuzhu Copilot"
#define MyAppVersion "6.0.0"
#define MyAppPublisher "zhutianliang"
#define MyAppExeName "zhuzhu Copilot.exe"
; 旧版标识（WinAppMigrator）：仅用于卸载时清理遗留的注册表分支与用户数据目录
#define MyLegacySlug "WinAppMigrator"
#define MyLegacyDataDir ".winapp_migrator"
#define MyLegacyExeName "WinAppMigrator.exe"
; 用户数据目录名（与 src/zhuzhu_Copilot/app_identity.py 的 DATA_DIR_NAME 保持一致）
#define MyDataDir ".zhuzhu_Copilot"

[Setup]
AppId={{B4A8C5D2-7E1F-4A3B-9C6D-8E2F5A7B1C3D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir=..\dist
OutputBaseFilename=zhuzhu Copilot Setup
SetupIconFile=..\assets\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 启用 Inno 内置的「选择安装语言」对话框：用户在安装前选择语言后，
; 整个向导（欢迎/选目录/选任务/安装中/完成）与所有自定义消息自动切换为对应语言。
; 之前禁用是因为旧代码在 InitializeSetup 阶段访问 WizardForm 导致 RuntimeError，
; 现已修复（相关代码移至 InitializeWizard / CurStepChanged）。
ShowLanguageDialog=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
; 安装包自身的文件属性版本号（资源管理器/属性页的文件版本字段）：
; 不设置时 Inno 只写 ProductVersion，FileVersion 会显示为空，与主程序版本不一致。
VersionInfoVersion={#MyAppVersion}

; ── 卸载器签名（必须编译期完成）──────────────────────────────────────────
; Inno 只在「卸载器 EXE 本身带数字签名」时把语言文本外置成 unins000.msg
; （官方说明：It cannot embed the messages into the EXE file because doing so
; would invalidate the digital signature）。因此：
;   · 启用 SignedUninstaller=yes + SignTool ⇒ Inno 在编译期签好卸载器桩，
;     安装时正常写出 unins000.msg，卸载可正常进行；
;   · 反过来，若装完之后再用脚本补签 unins000.exe，卸载器运行时会发现
;     「我已签名」而改去读外部 unins000.msg —— 该文件并不存在，于是卸载
;     当场中止（退出码 0、什么都不删、连日志都写不出），
;     UAC 里也会表现为「签名无效/未知发布者」。切勿再恢复那种做法。
; SignTool 名称与命令由 build_release.ps1 以
;   ISCC -szhuzhu_sign=... /DSIGNTOOL=zhuzhu_sign 传入
; （Inno 要求签名工具只能定义在编译器 IDE 或命令行上）。
#ifdef SIGNTOOL
SignedUninstaller=yes
SignTool={#SIGNTOOL}
#endif

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
; 英文安装向导：使用本地 English.isl（本 Inno Setup 7 beta 的 Default.isl 被本地化为中文，
; compiler:Default.isl 会显示为"简体中文"而非 English）。用户选 English 后，
; 安装结束会写 {app}\app_lang.ini，主程序据此默认英文界面。
Name: "english"; MessagesFile: "English.isl"

[CustomMessages]
; 语言选择页（安装前挑界面语言，决定主程序默认语言）
; 注意：自定义消息的语言前缀必须与 [Languages] 里的 Name 完全一致，
; 否则编译报 "Unknown language name"。
chinesesimplified.TWizardLanguagePrompt=选择 %(app)s 的界面语言：
chinesesimplified.TWizardLanguageSubPrompt=所选语言将作为 %(app)s 的界面语言，之后可在「设置」中随时更改。
chinesesimplified.TWizardLanguageTitle=界面语言
chinesesimplified.TLangZh=简体中文
chinesesimplified.TLangEn=English
chinesesimplified.TDesktopIcon=在桌面创建快捷方式
chinesesimplified.TAdditionalIcons=附加图标：
chinesesimplified.TInstallRootCert=正在安装受信任根证书...
chinesesimplified.TInstallTrustedPublisher=正在安装受信任发布者...
chinesesimplified.TInstallVCx64=正在安装 VC++ 运行库 (x64)...
chinesesimplified.TInstallVCx86=正在安装 VC++ 运行库 (x86)...
chinesesimplified.TInstallD3D=正在补装 d3dcompiler_47.dll...
chinesesimplified.TLaunchApp=立即启动 %(app)s
english.TWizardLanguagePrompt=Select the interface language for %(app)s:
english.TWizardLanguageSubPrompt=The chosen language is used for the %(app)s interface. You can change it later in Settings.
english.TWizardLanguageTitle=Interface Language
english.TLangZh=简体中文
english.TLangEn=English
english.TDesktopIcon=Create a desktop shortcut
english.TAdditionalIcons=Additional icons:
english.TInstallRootCert=Installing trusted root certificate...
english.TInstallTrustedPublisher=Installing trusted publisher certificate...
english.TInstallVCx64=Installing VC++ runtime (x64)...
english.TInstallVCx86=Installing VC++ runtime (x86)...
english.TInstallD3D=Installing d3dcompiler_47.dll...
english.TLaunchApp=Launch %(app)s now

[Tasks]
Name: "desktopicon"; Description: "{cm:TDesktopIcon}"; GroupDescription: "{cm:TAdditionalIcons}"

[Files]
Source: "..\dist\zhuzhu Copilot\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\zhuzhu Copilot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 受信任根证书：安装期复制到临时目录，装入本机受信任根/受信任发布者（UAC 信任）。
; 注意这里【不再】携带签名私钥（*.pfx）与补签脚本 —— 卸载器与安装包都在编译期
; 由 build_release.ps1 传递的 SignTool 签好，私钥无需随安装包分发。
Source: "..\build\certs\zhutianliang.cer"; DestDir: "{tmp}"; Flags: dontcopy
; VC++ 2015-2022 运行库（x64 + x86）：精简系统（如希沃白板 Win10）缺依赖时自动静默补装。
; 本应用为 x64，但精简系统常把 32 位运行库一并裁掉，装上以防系统侧组件异常。
Source: "..\build\redist\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: dontcopy
Source: "..\build\redist\vc_redist.x86.exe"; DestDir: "{tmp}"; Flags: dontcopy
; d3dcompiler_47.dll（精简 Win10/11 常缺，QtWebEngine/Chromium shader 编译必需）：
; 系统缺失时补装到 System32；应用 _internal 目录内同时随包携带一份，双保险。
Source: "..\build\redist\d3dcompiler_47.dll"; DestDir: "{tmp}"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; 卸载时清除本程序在注册表里的用户数据（HKCU\Software\zhuzhu_Copilot，QSettings 存储）。
; 此前卸载只删 %USERPROFILE%\.zhuzhu_Copilot（数据目录），注册表分支会残留下来并不断
; 累积陈旧 UI 偏好（主题/外观等），重装后读到的是上一轮的旧值。
; 现在卸载即清空注册表分支，与「卸载清除用户数据」的行为保持一致。
Root: HKCU; Subkey: "Software\zhuzhu_Copilot"; Flags: uninsdeletekey
; 旧版分支（HKCU\Software\WinAppMigrator）一并清理：本安装包为原地升级（AppId 不变），
; 旧分支已无人读取，清掉可避免注册表里长期残留旧名。
Root: HKCU; Subkey: "Software\{#MyLegacySlug}"; Flags: uninsdeletekey

[Run]
; 1. 将自签名根证书装入【机器级】受信任根与受信任发布者，使本机 UAC 信任
;    由 zhutianliang 签名的程序。注意必须用 -machine（LocalMachine 存储）：
;    UAC 弹窗（consent.exe 提升上下文）验证签名时只读取 LocalMachine 信任，
;    certutil 默认装入 CurrentUser，UAC 读不到 → 弹窗显示"签名失效/发布者未知"。
Filename: "{sys}\certutil.exe"; Parameters: "-f -machine -addstore Root ""{tmp}\zhutianliang.cer"""; StatusMsg: "{cm:TInstallRootCert}"; Flags: runhidden; BeforeInstall: ExtractTemporaryFile('zhutianliang.cer')
Filename: "{sys}\certutil.exe"; Parameters: "-f -machine -addstore TrustedPublisher ""{tmp}\zhutianliang.cer"""; StatusMsg: "{cm:TInstallTrustedPublisher}"; Flags: runhidden; BeforeInstall: ExtractTemporaryFile('zhutianliang.cer')
; 2. 系统缺 VC++ 运行库时自动静默安装，保证主程序/卸载器正常加载（x64 + x86）
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "{cm:TInstallVCx64}"; Flags: runhidden; BeforeInstall: ExtractTemporaryFile('vc_redist.x64.exe'); Check: not VCInstalled64
Filename: "{tmp}\vc_redist.x86.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "{cm:TInstallVCx86}"; Flags: runhidden; BeforeInstall: ExtractTemporaryFile('vc_redist.x86.exe'); Check: not VCInstalled32
; 3. 系统缺 d3dcompiler_47.dll 时补装到 System32（QtWebEngine 渲染必需）
Filename: "{sys}\cmd.exe"; Parameters: "/c copy /y ""{tmp}\d3dcompiler_47.dll"" ""{sysnative}\d3dcompiler_47.dll"""; StatusMsg: "{cm:TInstallD3D}"; Flags: runhidden; BeforeInstall: ExtractTemporaryFile('d3dcompiler_47.dll'); Check: not D3DCompilerInstalled
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:TLaunchApp}"; Flags: nowait postinstall skipifsilent runascurrentuser

; 卸载时先结束主程序进程，避免安装目录文件被占用
;（旧版进程名一并结束：卸载发生在运行中的旧版程序上时，文件同样会被占用）
[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM ""{#MyAppExeName}"""; Flags: runhidden; RunOnceId: "killmain"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM ""{#MyLegacyExeName}"""; Flags: runhidden; RunOnceId: "killlegacy"

; 兜底删除安装目录（含应用运行时生成的额外文件）
[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
{ ---- 语言选择（安装前确定界面语言） -------------------------------- }
{ 内置语言对话框（ShowLanguageDialog=yes）在安装前弹出，用户选择后整个向导
  与所有自定义消息自动切换为对应语言。安装结束时把 ActiveLanguage 映射为
  界面语言码写入 app_lang.ini，主程序启动按
  「--lang 参数 > 环境变量 ZHUZHU_LANG > 注册表设置 > app_lang.ini > 系统区域」
  解析语言（见 core/i18n.py::_installed_lang），故装完即生效，
  用户之后仍可在「设置 → 通用与记忆 → 界面语言」里改。 }

{ 安装向导语言（english / chinesesimplified）-> 界面语言码 }
function LangCodeOf(ALang: String): String;
begin
  if ALang = 'english' then
    Result := 'en_US'
  else
    Result := 'zh_CN';
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
end;

procedure InitializeWizard();
begin
  { 语言选择由内置对话框处理，无需自定义页。此处保留空过程以维持事件钩子。 }
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  LangFile, LangCode: String;
begin
  if CurStep = ssPostInstall then
  begin
    { 以安装前内置语言对话框的选择为准，写入 app_lang.ini }
    LangCode := LangCodeOf(ActiveLanguage);
    LangFile := ExpandConstant('{app}\app_lang.ini');
    { 覆盖写：每次安装都以本次语言选择为准。
      写失败不阻断安装 —— 主程序在读不到该文件时会退回按系统区域判定。 }
    try
      if SaveStringToFile(LangFile,
          '# Written by setup: default interface language for zhuzhu Copilot first launch' + #13#10 +
          '# Can be changed later in Settings → General & Memory → Interface Language' + #13#10 +
          'Lang=' + LangCode + #13#10, False) then
        Log('Interface language written: ' + LangFile + ' (Lang=' + LangCode + ')');
    except
      Log('Failed to write app_lang.ini (ignored, app will fall back to system locale): ' + LangFile);
    end;
  end;
end;

{ 系统是否已安装 VC++ 2015-2022 运行库：以对应位宽 vcruntime140.dll 是否存在为判据 }
{ x64：64 位 System32 下；x86：SysWOW64（32 位视角的 System32）下 }
function VCInstalled64(): Boolean;
begin
  Result := FileExists(ExpandConstant('{sysnative}\vcruntime140.dll'));
end;

function VCInstalled32(): Boolean;
begin
  Result := FileExists(ExpandConstant('{sys}\SysWOW64\vcruntime140.dll'));
end;

{ 系统是否自带 d3dcompiler_47.dll（精简系统常被裁剪） }
function D3DCompilerInstalled(): Boolean;
begin
  Result := FileExists(ExpandConstant('{sysnative}\d3dcompiler_47.dll'));
end;

procedure RemoveUserDataDir(const DirName: String);
var
  UserProfile: String;
  DataDir: String;
  ResultCode: Integer;
begin
  UserProfile := GetEnv('USERPROFILE');
  if UserProfile = '' then
    Exit;
  DataDir := UserProfile + '\' + DirName;
  if DirExists(DataDir) then
    Exec(ExpandConstant('{cmd}'), '/c rd /s /q "' + DataDir + '"', '',
         SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  { 卸载完成后清除用户数据目录（技能/对话记录/全部配置），
    并顺带清掉旧版目录（应用启动时会自动迁移旧目录，此处兜底防止残留旧名） }
  if CurUninstallStep = usPostUninstall then
  begin
    RemoveUserDataDir('{#MyDataDir}');
    RemoveUserDataDir('{#MyLegacyDataDir}');
  end;
end;