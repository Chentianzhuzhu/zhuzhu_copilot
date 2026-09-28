<#
.SYNOPSIS
    zhuzhu Copilot 发布流水线（重构版 · 单一入口 · 全 PowerShell）

.DESCRIPTION
    一条命令完成：版本号同步 → 运行时/依赖组件准备 → 工作流种子 → 清理 → 打包 →
    签名（主程序先于安装包签名）→ 生成 Inno Setup 安装包 → 签名 → 产物自检。

    相对旧脚本的改进：
      1. 单一入口：旧的 build_sign.ps1 / installer\build_setup.bat 退化为薄壳，只调本脚本，
         不再各自维护一套流程（此前两者步骤不一致：.bat 不签名、不生成工作流种子）。
      2. 构建解释器自动探测：不再盲信 PATH 上的 python（沙盒/系统里可能装着缺少
         PyInstaller 的解释器），自动挑选同时具备 PyInstaller + PyQt6 的解释器。
      3. 版本号一处生效：-Version 5.1.2 自动同步 installer\zhuzhu_Copilot.iss、
         src\zhuzhu_Copilot\update_check.py（APP_VERSION）、build\version_info.txt
         （exe 文件属性版本），并回读校验。
      4. 产物自检：打包后核对 exe 内是否含关键模块（含 ui.onboarding —— 曾漏打包导致
         「首次安装新手指南不弹」）与文件属性版本号，不合格即刻失败。
      5. 分阶段可跳过（-SkipApp/-SkipInstaller/...）、可演练（-DryRun）、全程日志留档。

    前置依赖（缺失会给出明确指引并在体检阶段失败）：
      · Python 3.13 + PyInstaller + PyQt6（其余依赖随应用需要）
      · Inno Setup 7（默认路径 C:\Program Files\Inno Setup 7\ISCC.exe）
      · 代码签名证书（CurrentUser\My 内 Subject 含 zhutianliang 且带私钥）

.PARAMETER Version
    目标版本号，如 5.1.2。提供则同步三处版本号；省略则沿用 installer\zhuzhu_Copilot.iss 现值。

.PARAMETER DryRun
    只做体检（工具链/证书/前置文件）+ 版本同步，不执行任何构建。

.PARAMETER NoSign
    跳过签名（生成未签名产物，用于本地功能验证）。

.EXAMPLE
    .\build_release.ps1 -Version 5.1.2
    完整打包 + 签名，产物版本 5.1.2。

.EXAMPLE
    .\build_release.ps1 -DryRun
    只体检，不构建。

.EXAMPLE
    .\build_release.ps1 -SkipRuntime -NoSign
    复用已有运行时包、不签名，快速出安装包（内部调试用）。
#>
[CmdletBinding()]
param(
    [string]$Version,
    [string]$ProjectDir,
    [string]$Python,
    [string]$CertSubject = 'zhutianliang',
    [string]$TimestampServer = 'http://timestamp.digicert.com',
    [string]$PfxPassword = 'Zhutianliang.2026',
    [string]$Iscc = 'C:\Program Files\Inno Setup 7\ISCC.exe',
    [switch]$NoSign,
    [switch]$SkipRuntime,
    [switch]$SkipSeed,
    [switch]$SkipApp,
    [switch]$SkipInstaller,
    [switch]$KeepDist,
    [switch]$DryRun,
    [switch]$NoLog
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

# 项目目录：脚本所在目录。
# 注意：不能写在 param 默认值里（PowerShell 5.1 求值默认值时 $PSScriptRoot 还是空，
# 会直接报 “Cannot bind argument to parameter 'Path'”），必须在脚本体内解析。
if (-not $ProjectDir) {
    $ProjectDir = if ($PSCommandPath) { Split-Path -Parent $PSCommandPath }
                  elseif ($MyInvocation.MyCommand.Path) { Split-Path -Parent $MyInvocation.MyCommand.Path }
                  else { (Get-Location).Path }
}
$ProjectDir = (Resolve-Path $ProjectDir).Path

# 子进程 Python 告警统一静默：避免 py 的 UserWarning 混进探测输出被误判为异常
$env:PYTHONWARNINGS = 'ignore'

$Script:StepNo = 0
$Script:StepTotal = 0
$Script:Started = Get-Date
$Script:Warnings = New-Object System.Collections.ArrayList

# ---------------------------------------------------------------- 输出helpers
function Write-Head([string]$text) {
    Write-Host ''
    Write-Host ('=' * 68) -ForegroundColor DarkCyan
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ('=' * 68) -ForegroundColor DarkCyan
}
function Write-Stage([string]$text) {
    $Script:StepNo++
    Write-Host ''
    Write-Host ("[$($Script:StepNo)/$($Script:StepTotal)] $text") -ForegroundColor Yellow
    Write-Host ('-' * 68) -ForegroundColor DarkGray
}
function Write-Ok([string]$text)   { Write-Host "  [OK]   $text" -ForegroundColor Green }
function Write-Info([string]$text) { Write-Host "  [..]   $text" -ForegroundColor Gray }
function Write-Warn([string]$text) {
    Write-Host "  [WARN] $text" -ForegroundColor DarkYellow
    [void]$Script:Warnings.Add($text)
}
function Write-Err([string]$text)  { Write-Host "  [FAIL] $text" -ForegroundColor Red }
function Fail([string]$text) {
    Write-Err $text
    Write-Host ''
    Write-Host "构建失败。日志: $Script:LogPath" -ForegroundColor Red
    throw $text
}

# 运行外部命令并校验退出码（GUI 子系统程序需经 cmd /c 才能同步等待）
# 注意：native 程序写入 stderr 时，在 $ErrorActionPreference='Stop' 下 PowerShell 5.1 会
# 抛 NativeCommandError 终止脚本（PyInstaller/依赖解析都会写 stderr）—— 故本地降级为
# Continue，只以退出码判断成败。
function Invoke-Exe {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [string[]]$Arguments = @(),
        [switch]$ViaCmd,
        [string]$WorkDir
    )
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    if ($WorkDir) { Push-Location $WorkDir }
    try {
        # 【必须 Out-Host】PowerShell 函数会把未捕获的输出并入返回值：若直接调用外部程序，
        # 子进程 stdout 会与退出码一起返回（曾把「0」和一大段日志当成退出码，误判失败）。
        # 这里把子进程输出直通控制台（同时进 transcript 日志），函数只返回整数退出码。
        if ($ViaCmd) {
            # GUI 子系统 exe（ISCC）：PowerShell 直接调用不等待且 LASTEXITCODE 为空，
            # 必须走 cmd /c 同步等待并拿到真实退出码。
            $line = '"' + $Exe + '"'
            foreach ($a in $Arguments) {
                $line += ' ' + ($(if ($a -match '\s') { '"' + $a + '"' } else { $a }))
            }
            & cmd.exe /c $line 2>&1 | Out-Host
        } else {
            & $Exe @Arguments 2>&1 | Out-Host
        }
        return [int]$LASTEXITCODE
    } finally {
        if ($WorkDir) { Pop-Location }
        $ErrorActionPreference = $old
    }
}

# 采集外部命令输出（stdout+stderr 合并），供探针/版本探测做正则解析
function Invoke-NativeCapture {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [string[]]$Arguments = @()
    )
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = (& $Exe @Arguments 2>&1 | Out-String)
        return @{ Output = $out; Code = $LASTEXITCODE }
    } finally {
        $ErrorActionPreference = $old
    }
}

# ---------------------------------------------------------------- 版本号
function Get-ProjectVersion {
    $iss = Join-Path $ProjectDir 'installer\zhuzhu_Copilot.iss'
    $m = Select-String -Path $iss -Pattern '^#define\s+MyAppVersion\s+"([^"]+)"' |
        Select-Object -First 1
    if (-not $m) { Fail "无法从 installer\zhuzhu_Copilot.iss 读取 MyAppVersion" }
    return $m.Matches[0].Groups[1].Value
}

function Update-VersionFile {
    <#
      按规则替换某文件里的版本号，并回读校验；已是目标版本则跳过（幂等，不报假警告）。
      ReadPattern 必须只含一个捕获组（即版本号本身）。
    #>
    param(
        [Parameter(Mandatory)][string]$RelPath,
        [Parameter(Mandatory)][hashtable[]]$Rules,
        [Parameter(Mandatory)][string]$ReadPattern,
        [Parameter(Mandatory)][string]$Wanted
    )
    $p = Join-Path $ProjectDir $RelPath
    if (-not (Test-Path $p)) { Fail "版本号文件不存在: $RelPath" }

    $text = [IO.File]::ReadAllText($p)
    $cur = ([regex]::Match($text, $ReadPattern)).Groups[1].Value
    if (-not $cur) { Fail "$RelPath 中未匹配到版本号（请核对文件格式）" }
    if ($cur -eq $Wanted) {
        Write-Info "$RelPath 已是 $Wanted（无需替换）"
        return
    }

    foreach ($r in $Rules) { $text = [regex]::Replace($text, $r.Pattern, $r.Replace) }
    [IO.File]::WriteAllText($p, $text)

    $now = ([regex]::Match([IO.File]::ReadAllText($p), $ReadPattern)).Groups[1].Value
    if ($now -ne $Wanted) { Fail "$RelPath 版本写入校验失败：读回 '$now'，期望 '$Wanted'" }
    Write-Ok "$RelPath  $cur -> $Wanted"
}

function Set-ProjectVersion([string]$v) {
    if ($v -notmatch '^\d+\.\d+\.\d+$') {
        Fail "版本号格式应为 x.y.z（收到：$v）"
    }
    $parts = $v.Split('.')
    $quad = "$($parts[0]), $($parts[1]), $($parts[2]), 0"   # x.y.z -> x, y, z, 0（四段文件版本）

    # 1) Inno Setup 安装包版本（向导/控制面板显示）
    Update-VersionFile -RelPath 'installer\zhuzhu_Copilot.iss' -Wanted $v `
        -ReadPattern '(?m)^#define\s+MyAppVersion\s+"([^"]+)"' `
        -Rules @(@{ Pattern = '(?m)^(#define\s+MyAppVersion\s+")[^"]+(")'
                   Replace = ('${1}' + $v + '${2}') })

    # 2) 客户端运行时版本（自动更新与服务器比对的关键字段）
    Update-VersionFile -RelPath 'src\zhuzhu_Copilot\update_check.py' -Wanted $v `
        -ReadPattern '(?m)^APP_VERSION\s*=\s*"([^"]+)"' `
        -Rules @(@{ Pattern = '(?m)^(APP_VERSION\s*=\s*")[^"]+(")'
                   Replace = ('${1}' + $v + '${2}') })

    # 3) exe 文件属性版本（资源管理器属性页 / 安装包内嵌版本资源）
    Update-VersionFile -RelPath 'build\version_info.txt' -Wanted $v `
        -ReadPattern "u'ProductVersion',\s*u'([^']+)'" `
        -Rules @(
            @{ Pattern = 'filevers=\([^)]*\)'; Replace = "filevers=($quad)" },
            @{ Pattern = 'prodvers=\([^)]*\)'; Replace = "prodvers=($quad)" },
            @{ Pattern = "(u'FileVersion',\s*u')[^']*(')";    Replace = ('${1}' + $v + '${2}') },
            @{ Pattern = "(u'ProductVersion',\s*u')[^']*(')"; Replace = ('${1}' + $v + '${2}') }
        )

    Write-Ok "版本号已同步为 $v（iss / update_check.py / version_info.txt）"
}

# ---------------------------------------------------------------- 体检
function Resolve-BuildPython {
    # 候选顺序：显式指定 > 常见独立安装 > PATH > py 启动器
    # （沙盒/应用自带的便携解释器常出现在 PATH 上且缺少 PyInstaller，故需逐个探测）
    $cands = New-Object System.Collections.ArrayList
    if ($Python) { [void]$cands.Add($Python) }
    foreach ($p in @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        'C:\Python313\python.exe',
        'C:\Python312\python.exe')) {
        if (Test-Path $p) { [void]$cands.Add($p) }
    }
    $onPath = (Get-Command python -ErrorAction SilentlyContinue).Source
    if ($onPath) { [void]$cands.Add($onPath) }
    $pyLauncher = (Get-Command py -ErrorAction SilentlyContinue).Source
    if ($pyLauncher) { [void]$cands.Add($pyLauncher + ' -3.13') }

    foreach ($c in $cands) {
        $exe = $c
        $pre = @()
        if ($c -like '* -3.13') { $exe = $c.Split(' ')[0]; $pre = @('-3.13') }
        if (-not (Test-Path $exe)) { continue }
        $probe = @($pre) + @('-c', 'import PyInstaller, PyQt6; print("PROBE_OK")')
        $r = Invoke-NativeCapture -Exe $exe -Arguments $probe
        if ($r.Output -match 'PROBE_OK') {
            $v = Invoke-NativeCapture -Exe $exe -Arguments (@($pre) + @('-c', 'import sys;print(sys.version.split()[0])'))
            $ver = [regex]::Match($v.Output, '\d+\.\d+\.\d+')
            Write-Ok ("构建解释器: {0} (Python {1})" -f $exe, $(if ($ver.Success) { $ver.Value } else { '?' }))
            return @{ Exe = $exe; Pre = $pre }
        }
    }
    Fail ("未找到同时具备 PyInstaller + PyQt6 的 Python 解释器。" +
          "请安装依赖（pip install pyinstaller PyQt6）或用 -Python 指定解释器路径。")
}

function Test-Prereq($PyInfo) {
    $fail = New-Object System.Collections.ArrayList

    foreach ($p in @(
        'src\main.py',
        'build\zhuzhu_Copilot.spec',
        'scripts\generate_spec.py',
        'scripts\verify_release_artifact.py',
        'installer\zhuzhu_Copilot.iss',
        'assets\icon.ico',
        'assets\admin.manifest')) {
        if (Test-Path (Join-Path $ProjectDir $p)) {
            Write-Ok "存在 $p"
        } else {
            [void]$fail.Add("缺少文件: $p")
        }
    }

    if (Test-Path $Iscc) {
        Write-Ok "Inno Setup: $Iscc"
    } else {
        [void]$fail.Add("找不到 ISCC.exe: $Iscc（请安装 Inno Setup 7 或用 -Iscc 指定）")
    }

    if ($NoSign) {
        Write-Warn '已指定 -NoSign：产物不签名（仅用于本地验证）'
    } else {
        $cert = Get-ChildItem Cert:\CurrentUser\My |
            Where-Object { $_.Subject -like "*$CertSubject*" -and $_.HasPrivateKey } |
            Select-Object -First 1
        if ($cert) {
            Write-Ok "签名证书: $($cert.Subject)（有效期至 $($cert.NotAfter.ToString('yyyy-MM-dd'))）"
        } else {
            [void]$fail.Add("CurrentUser\My 中找不到含 '$CertSubject' 且带私钥的证书")
        }
    }

    # 构建解释器自带依赖自检（缺依赖会让打包出的产物功能残缺）
    # 用单行 -c（多行脚本经 PowerShell 传参会被折行破坏）并以 MISS: 标记输出
    $req = 'PyInstaller,PyQt6,PIL,docx,pptx,openpyxl,pygame,win32api'
    $probeSrc = "import importlib.util as u;mods='" + $req +
    "'.split(',');print('MISS:'+','.join([m for m in mods if u.find_spec(m) is None]))"
    $probe = @($PyInfo.Pre) + @('-c', $probeSrc)
    # 只认带 MISS: 标记的那一行，避免告警/其他输出混入被误判为缺失
    $r = Invoke-NativeCapture -Exe $PyInfo.Exe -Arguments $probe
    $out = $r.Output
    $m = [regex]::Match($out, 'MISS:([^\r\n]*)')
    if (-not $m.Success) {
        Write-Warn "依赖自检未能取得结果，跳过（原始输出首行：$((($out -split "\r?\n") | Where-Object { $_.Trim() } | Select-Object -First 1))）"
    } elseif ($m.Groups[1].Value.Trim()) {
        [void]$fail.Add("构建解释器缺少依赖模块: $($m.Groups[1].Value.Trim())（pip install 后重试）")
    } else {
        Write-Ok "构建解释器依赖齐全（$req）"
    }

    if ($fail.Count -gt 0) {
        Write-Host ''
        foreach ($f in $fail) { Write-Err $f }
        Fail "环境体检未通过（$($fail.Count) 项）"
    }
}

# ---------------------------------------------------------------- 主流程
Set-Location $ProjectDir
$Script:LogPath = Join-Path $ProjectDir ("build\build_{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))
New-Item -ItemType Directory -Force -Path (Split-Path $Script:LogPath) | Out-Null

Write-Head 'zhuzhu Copilot 发布流水线'
Write-Host "  项目目录: $ProjectDir"
Write-Host "  开始时间: $($Script:Started.ToString('yyyy-MM-dd HH:mm:ss'))"
Write-Host "  日志文件: $Script:LogPath"
if (-not $NoLog) {
    try { Start-Transcript -Path $Script:LogPath -Force | Out-Null } catch { }
}

try {
    # 阶段计数（供 [n/N] 展示；跳过的不计入）
    $stages = @('环境体检')
    if ($Version)      { $stages += '版本号同步' }
    if (-not $SkipRuntime)   { $stages += '沙盒运行时打包' }
    if (-not $SkipRuntime)   { $stages += '依赖组件准备（VC++/d3dcompiler）' }
    if (-not $SkipSeed)      { $stages += '工作流种子快照' }
    if (-not $SkipApp)       { $stages += '清理旧产物' }
    if (-not $SkipApp)       { $stages += '生成 spec + 打包主程序' }
    if (-not $SkipApp -and -not $NoSign) { $stages += '签名主程序' }
    if (-not $SkipInstaller) { $stages += '编译安装包（Inno Setup）' }
    if (-not $SkipInstaller -and -not $NoSign) { $stages += '签名安装包' }
    if (-not $SkipInstaller) { $stages += '产物自检 + 汇总' }
    $Script:StepTotal = $stages.Count
    $Script:StepNo = 0

    # ---- 1. 体检 -------------------------------------------------------
    Write-Stage '环境体检'
    $PyInfo = Resolve-BuildPython
    Test-Prereq $PyInfo
    $r = Invoke-NativeCapture -Exe $PyInfo.Exe -Arguments (@($PyInfo.Pre) + @('-m', 'PyInstaller', '--version'))
    $m = [regex]::Match($r.Output, '\d+\.\d+(\.\d+)?')
    Write-Ok "PyInstaller $(if ($m.Success) { $m.Value } else { '（版本未知）' })"

    if (-not $Version) {
        $Version = Get-ProjectVersion
        Write-Info "未指定 -Version，沿用当前版本：$Version"
    }
    Write-Ok "目标版本: $Version"

    if ($DryRun) {
        Write-Host ''
        Write-Host '  -DryRun：体检完成，未执行构建。' -ForegroundColor Cyan
        return
    }

    # ---- 2. 版本号同步 -------------------------------------------------
    if ($Version) {
        Write-Stage "版本号同步 -> $Version"
        Set-ProjectVersion $Version      # 幂等：已是目标版本则原样保留
    }

    # ---- 3. 沙盒运行时 -------------------------------------------------
    if (-not $SkipRuntime) {
        Write-Stage '沙盒运行时打包（Node/Python 便携版）'
        $rc = Invoke-Exe -Exe $PyInfo.Exe -Arguments (@($PyInfo.Pre) + @('scripts\prepare_runtime_bundle.py'))
        if ($rc -ne 0) { Fail "prepare_runtime_bundle.py 失败（退出码 $rc）" }
        Write-Ok '沙盒运行时就绪'
    } else { Write-Warn '已跳过：沙盒运行时打包' }

    # ---- 4. 依赖组件（VC++ 运行库 / d3dcompiler_47）--------------------
    if (-not $SkipRuntime) {
        Write-Stage '依赖组件准备（VC++ 运行库 / d3dcompiler_47）'
        $redistDir = Join-Path $ProjectDir 'build\redist'
        New-Item -ItemType Directory -Force -Path $redistDir | Out-Null
        $redist = @(
            @{ Name = 'vc_redist.x64.exe'; Url = 'https://aka.ms/vs/17/release/vc_redist.x64.exe' },
            @{ Name = 'vc_redist.x86.exe'; Url = 'https://aka.ms/vs/17/release/vc_redist.x86.exe' }
        )
        foreach ($r in $redist) {
            $dest = Join-Path $redistDir $r.Name
            if (Test-Path $dest) { Write-Info "$($r.Name) 已存在，复用"; continue }
            Write-Info "下载 $($r.Name) ..."
            try {
                $old = $ProgressPreference; $ProgressPreference = 'SilentlyContinue'
                Invoke-WebRequest -Uri $r.Url -OutFile $dest -UseBasicParsing -TimeoutSec 600
                $ProgressPreference = $old
            } catch {
                $curl = (Get-Command curl.exe -ErrorAction SilentlyContinue).Source
                if ($curl) { & $curl -L -o $dest $r.Url --max-time 600 } else { throw }
            }
            if (-not (Test-Path $dest)) { Fail "下载失败: $($r.Name)（可手动放入 build\redist 后重试）" }
            Write-Ok "已准备 $($r.Name)"
        }
        $d3d = Join-Path $redistDir 'd3dcompiler_47.dll'
        if (Test-Path $d3d) {
            Write-Info 'd3dcompiler_47.dll 已存在，复用'
        } else {
            $srcD3d = "$env:SystemRoot\System32\d3dcompiler_47.dll"
            if (Test-Path $srcD3d) {
                Copy-Item $srcD3d $d3d -Force
                Write-Ok '已从 System32 提取 d3dcompiler_47.dll'
            } else {
                Write-Warn 'System32 无 d3dcompiler_47.dll（精简系统上 QtWebEngine 可能异常）'
            }
        }
    }

    # ---- 5. 工作流种子 -------------------------------------------------
    if (-not $SkipSeed) {
        Write-Stage '工作流种子快照（现成工作流 + 团队配置）'
        $rc = Invoke-Exe -Exe $PyInfo.Exe -Arguments (@($PyInfo.Pre) + @('scripts\prepare_workflow_seed.py'))
        if ($rc -ne 0) { Fail "prepare_workflow_seed.py 失败（退出码 $rc）" }
        Write-Ok '工作流种子就绪'
    } else { Write-Warn '已跳过：工作流种子快照' }

    # ---- 6. 清理 -------------------------------------------------------
    if (-not $SkipApp) {
        Write-Stage '清理旧产物'
        if (-not $KeepDist) {
            Remove-Item -Recurse -Force (Join-Path $ProjectDir 'dist') -ErrorAction SilentlyContinue
            Write-Ok '已清理 dist\'
        } else {
            Write-Info '保留 dist\（-KeepDist）'
        }
        Remove-Item -Recurse -Force (Join-Path $ProjectDir 'build\__pycache__') -ErrorAction SilentlyContinue
        Write-Ok '已清理 build\__pycache__'
    }

    # ---- 7. 生成 spec + 打包主程序 --------------------------------------
    if (-not $SkipApp) {
        Write-Stage '生成 spec + 打包主程序'
        # spec 由 scripts/generate_spec.py 单一来源生成，避免外部写回的中间态参与构建
        $rc = Invoke-Exe -Exe $PyInfo.Exe -Arguments (@($PyInfo.Pre) + @('scripts\generate_spec.py'))
        if ($rc -ne 0) { Fail "generate_spec.py 失败（退出码 $rc）" }
        Write-Ok 'build\zhuzhu_Copilot.spec 已按仓库配置重新生成'

        $t = Get-Date
        $rc = Invoke-Exe -Exe $PyInfo.Exe -Arguments (@($PyInfo.Pre) +
            @('-m', 'PyInstaller', '--clean', '--noconfirm', 'build\zhuzhu_Copilot.spec'))
        if ($rc -ne 0) { Fail "PyInstaller 打包失败（退出码 $rc）" }
        $mainExe = Join-Path $ProjectDir 'dist\zhuzhu Copilot\zhuzhu Copilot.exe'
        if (-not (Test-Path $mainExe)) { Fail "未生成主程序: $mainExe" }
        $sz = [math]::Round((Get-Item $mainExe).Length / 1MB, 1)
        Write-Ok ("主程序打包完成（耗时 {0:F1} 分钟，exe {1} MB）" -f ((Get-Date) - $t).TotalMinutes, $sz)
    }

    # ---- 8. 签名主程序（必须先于安装包编译）----------------------------
    $cert = $null
    if (-not $NoSign) {
        $cert = Get-ChildItem Cert:\CurrentUser\My |
            Where-Object { $_.Subject -like "*$CertSubject*" -and $_.HasPrivateKey } |
            Select-Object -First 1
    }
    if (-not $SkipApp -and -not $NoSign -and $cert) {
        Write-Stage '签名主程序'
        $mainExe = Join-Path $ProjectDir 'dist\zhuzhu Copilot\zhuzhu Copilot.exe'
        Set-AuthenticodeSignature -FilePath $mainExe -Certificate $cert `
            -HashAlgorithm SHA256 -TimestampServer $TimestampServer | Out-Null
        $sig = Get-AuthenticodeSignature $mainExe
        if ($sig.Status -ne 'Valid') { Fail "主程序签名无效: $($sig.Status)（$($sig.StatusMessage)）" }
        Write-Ok "主程序已签名并带时间戳（$($sig.Status)）"

        # 导出私钥供安装器补签 unins000.exe（密码与 .iss 的 /DMyAppPwd 一致）
        $pfx = Join-Path $ProjectDir 'build\certs\zhutianliang.pfx'
        New-Item -ItemType Directory -Force -Path (Split-Path $pfx) | Out-Null
        if (Test-Path $pfx) { Remove-Item -Force $pfx }
        $sec = ConvertTo-SecureString $PfxPassword -AsPlainText -Force
        $cert | Export-PfxCertificate -FilePath $pfx -Password $sec -ErrorAction Stop | Out-Null
        Write-Ok "已导出安装器签名私钥: build\certs\zhutianliang.pfx"
    } elseif (-not $SkipApp) {
        Write-Warn '已跳过主程序签名'
    }

    # ---- 9. 编译安装包 -------------------------------------------------
    if (-not $SkipInstaller) {
        Write-Stage '编译安装包（Inno Setup 7）'
        $iss = Join-Path $ProjectDir 'installer\zhuzhu_Copilot.iss'
        $rc = Invoke-Exe -Exe $Iscc -Arguments @($iss, "/DMyAppPwd=$PfxPassword") -ViaCmd
        if ($rc -ne 0) { Fail "ISCC 编译失败（退出码 $rc）" }
        $setup = Join-Path $ProjectDir 'dist\zhuzhu Copilot Setup.exe'
        if (-not (Test-Path $setup)) { Fail "未生成安装包: $setup" }
        $sz = [math]::Round((Get-Item $setup).Length / 1MB, 1)
        Write-Ok "安装包已生成（$sz MB）"
    }

    # ---- 10. 签名安装包 ------------------------------------------------
    if (-not $SkipInstaller -and -not $NoSign -and $cert) {
        Write-Stage '签名安装包'
        $setup = Join-Path $ProjectDir 'dist\zhuzhu Copilot Setup.exe'
        Set-AuthenticodeSignature -FilePath $setup -Certificate $cert `
            -HashAlgorithm SHA256 -TimestampServer $TimestampServer | Out-Null
        $sig = Get-AuthenticodeSignature $setup
        if ($sig.Status -ne 'Valid') { Fail "安装包签名无效: $($sig.Status)（$($sig.StatusMessage)）" }
        Write-Ok "安装包已签名并带时间戳（$($sig.Status)）"
    }

    # ---- 11. 产物自检 + 汇总 -------------------------------------------
    if (-not $SkipInstaller) {
        Write-Stage '产物自检 + 汇总'
        $mainExe = Join-Path $ProjectDir 'dist\zhuzhu Copilot\zhuzhu Copilot.exe'
        $setup = Join-Path $ProjectDir 'dist\zhuzhu Copilot Setup.exe'

        # 11.1 exe 文件属性版本必须等于目标版本
        $fv = (Get-Item $mainExe).VersionInfo
        Write-Info "exe 文件属性版本: FileVersion=$($fv.FileVersion) ProductVersion=$($fv.ProductVersion)"
        if ($fv.FileVersion -ne $Version) {
            Fail "exe 文件属性版本与目标不符（$($fv.FileVersion) != $Version），请检查 build\version_info.txt"
        }
        Write-Ok "exe 版本号校验通过（$Version）"

        # 11.2 关键模块随包自检（含 ui.onboarding：曾漏打包导致新手指南不弹）
        $rc = Invoke-Exe -Exe $PyInfo.Exe -Arguments (@($PyInfo.Pre) +
            @('scripts\verify_release_artifact.py', $mainExe))
        if ($rc -ne 0) { Fail '产物自检未通过：关键模块缺失（详见上方输出）' }

        # 11.3 签名状态汇总
        foreach ($f in @($mainExe, $setup)) {
            if (Test-Path $f) {
                $sig = Get-AuthenticodeSignature $f
                $ver = if ($f -eq $mainExe) { (Get-Item $f).VersionInfo.FileVersion } else { $Version }
                Write-Info ("{0}  [v{1}]  签名={2}" -f (Split-Path $f -Leaf), $ver, $sig.Status)
            }
        }

        Write-Host ''
        Write-Host '  产物清单：' -ForegroundColor Cyan
        Get-ChildItem (Join-Path $ProjectDir 'dist') -Recurse -File | ForEach-Object {
            $rel = $_.FullName.Substring($ProjectDir.Length).TrimStart('\')
            Write-Host ("    {0}  ({1} MB)" -f $rel, [math]::Round($_.Length / 1MB, 2)) -ForegroundColor Gray
        }
    }

    # ---- 收尾 ----------------------------------------------------------
    $elapsed = (Get-Date) - $Script:Started
    Write-Head ("构建成功 · v$Version · 耗时 {0:mm\:ss}" -f $elapsed)
    Write-Host '  安装包: dist\zhuzhu Copilot Setup.exe' -ForegroundColor Green
    if ($Script:Warnings.Count -gt 0) {
        Write-Host "  警告 $($Script:Warnings.Count) 条：" -ForegroundColor DarkYellow
        foreach ($w in $Script:Warnings) { Write-Host "    - $w" -ForegroundColor DarkYellow }
    }
} catch {
    if (-not $Script:LogPath -or -not (Test-Path $Script:LogPath)) {
        Write-Err $_.Exception.Message
    }
    exit 1
} finally {
    if (-not $NoLog) { try { Stop-Transcript | Out-Null } catch { } }
}
