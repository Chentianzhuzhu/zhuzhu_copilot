; ╔══════════════════════════════════════════════════════════╗
; ║  zhuzhu Copilot - Inno Setup 7 安装脚本                 ║
; ║  前置：先运行 build 生成 dist\zhuzhu Copilot（已混淆签名）║
; ║  卸载：使用 Inno 自带卸载器（unins000.exe），卸载时        ║
; ║        结束主程序进程并清除用户数据目录                    ║
; ╚══════════════════════════════════════════════════════════╝

#define MyAppName "zhuzhu Copilot"
#define MyAppVersion "5.1.7"
#define MyAppPublisher "zhutianliang"
#define MyAppExeName "zhuzhu Copilot.exe"
; 旧版标识（WinAppMigrator）：仅用于卸载时清理遗留的注册表分支与用户数据目录
#define MyLegacySlug "WinAppMigrator"
#define MyLegacyDataDir ".winapp_migrator"
#define MyLegacyExeName "WinAppMigrator.exe"
; 用户数据目录名（与 src/zhuzhu_Copilot/app_identity.py 的 DATA_DIR_NAME 保持一致）
#define MyDataDir ".zhuzhu_Copilot"
; 签名私钥密码：由 build_sign.ps1 以 /DMyAppPwd=<密码> 传入，这里提供兜底默认
#ifndef MyAppPwd
#define MyAppPwd "Zhutianliang.2026"
#endif

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
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
; 安装包自身的文件属性版本号（资源管理器/属性页的文件版本字段）：
; 不设置时 Inno 只写 ProductVersion，FileVersion 会显示为空，与主程序版本不一致。
VersionInfoVersion={#MyAppVersion}

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "在桌面创建快捷方式"; GroupDescription: "附加图标："

[Files]
Source: "..\dist\zhuzhu Copilot\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\zhuzhu Copilot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 受信任根证书、签名私钥、卸载器补签脚本：安装期复制到临时目录使用
Source: "..\build\certs\zhutianliang.cer"; DestDir: "{tmp}"; Flags: dontcopy
Source: "..\build\certs\zhutianliang.pfx"; DestDir: "{tmp}"; Flags: dontcopy
Source: "..\scripts\sign_uninstaller.ps1"; DestDir: "{tmp}"; Flags: dontcopy
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
Filename: "{sys}\certutil.exe"; Parameters: "-f -machine -addstore Root ""{tmp}\zhutianliang.cer"""; StatusMsg: "正在安装受信任根证书..."; Flags: runhidden; BeforeInstall: ExtractTemporaryFile('zhutianliang.cer')
Filename: "{sys}\certutil.exe"; Parameters: "-f -machine -addstore TrustedPublisher ""{tmp}\zhutianliang.cer"""; StatusMsg: "正在安装受信任发布者..."; Flags: runhidden; BeforeInstall: ExtractTemporaryFile('zhutianliang.cer')
; 2. 用签名私钥补签 Inno 动态生成的卸载程序，避免 UAC 显示“未验证的发布者”
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{tmp}\sign_uninstaller.ps1"" -PfxPath ""{tmp}\zhutianliang.pfx"" -Password ""{#MyAppPwd}"" -Target ""{app}\unins000.exe"""; StatusMsg: "正在签名卸载程序..."; Flags: runhidden; BeforeInstall: ExtractSignFiles
; 3. 系统缺 VC++ 运行库时自动静默安装，保证主程序/卸载器正常加载（x64 + x86）
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "正在安装 VC++ 运行库 (x64)..."; Flags: runhidden; BeforeInstall: ExtractTemporaryFile('vc_redist.x64.exe'); Check: not VCInstalled64
Filename: "{tmp}\vc_redist.x86.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "正在安装 VC++ 运行库 (x86)..."; Flags: runhidden; BeforeInstall: ExtractTemporaryFile('vc_redist.x86.exe'); Check: not VCInstalled32
; 4. 系统缺 d3dcompiler_47.dll 时补装到 System32（QtWebEngine 渲染必需）
Filename: "{sys}\cmd.exe"; Parameters: "/c copy /y ""{tmp}\d3dcompiler_47.dll"" ""{sysnative}\d3dcompiler_47.dll"""; StatusMsg: "正在补装 d3dcompiler_47.dll..."; Flags: runhidden; BeforeInstall: ExtractTemporaryFile('d3dcompiler_47.dll'); Check: not D3DCompilerInstalled
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent runascurrentuser

; 卸载时先结束主程序进程，避免安装目录文件被占用
;（旧版进程名一并结束：卸载发生在运行中的旧版程序上时，文件同样会被占用）
[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM ""{#MyAppExeName}"""; Flags: runhidden; RunOnceId: "killmain"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM ""{#MyLegacyExeName}"""; Flags: runhidden; RunOnceId: "killlegacy"

; 兜底删除安装目录（含应用运行时生成的额外文件）
[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
// 卸载器补签所需文件（脚本 + 私钥）一次性从安装包提取到临时目录
procedure ExtractSignFiles();
begin
  ExtractTemporaryFile('sign_uninstaller.ps1');
  ExtractTemporaryFile('zhutianliang.pfx');
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