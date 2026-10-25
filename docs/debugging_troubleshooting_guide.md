# 调试与故障排查手册（根因 → 复现 → 修复 → 验证）

> **本文的验证等级声明（先看清再信）**
> 本手册编写时当前执行上下文**无命令执行权限**（`python -m pytest` / `python --version` 均被沙盒以「非白名单命令」拒绝），
> 因此**未跑过 pytest、未跑过构建**。每条结论都标注来源：
> `[静态·已核对]` = 已读到对应源码/配置/脚本原文；`[环境·已知风险]` = 来自项目已知风险面/构建说明，未实测。
> **凡标 [静态] 的，第一次到现场请按「验证」列跑一遍再下结论**——只接「已验证」，不接「应该是」。

---

## 0. 排查总原则

1. **先判定故障归属环节**，再动手：`测试 → 构建 → 安装/签名 → 运行时`。跨环节猜是最贵的错误。
2. **判据用退出码，不用日志末行**。构建、pytest 都有「打印看着漂亮、退出码非 0」的历史缺陷。
3. **日志位置先于分析**：没有日志时先说清"日志为什么没有"（见 §2.1、§3.1）。
4. 每修一处，回跑**同一支最小子集**用例复现确认，再跑全量。

环节归属速判：

| 症状 | 先看哪里 | 环节 |
|---|---|---|
| `No module named pytest` / import 失败 | pytest.ini `pythonpath=src`、是否在项目根运行 | 测试 |
| 全绿但退出码 `0xC0000005` / `0xC0000409` | tests/conftest.py 的 Qt 清理 | 测试 |
| 控制台打中文 `UnicodeEncodeError` | `PYTHONIOENCODING` | 测试 |
| `Source file ... does not exist` | `build\redist\`、`build\certs\` | 构建 |
| 构建末行 Success 却流程失败 | `build\build_*.log`，退出码 | 构建 |
| `No code-signing certificate ... matching` | `Cert:\CurrentUser\My` | 构建/签名 |
| 卸载器报 `unins000.msg is missing` | 是否事后补签过 `unins000.exe` | 安装 |
| UAC 显示「未知发布者/签名无效」 | 证书是否用 `-machine` 装到 LocalMachine | 安装 |
| 装了英文但界面中文 | 注册表 QSettings 覆盖 > `app_lang.ini` | 运行 |
| 插件「自己消失了」 | `<数据目录>\plugins\.guard_log.jsonl` | 运行 |
| 界面出现 `xxx.yyy` 键名或半中文 | `locales\en_US.json` 缺键 | 运行 |

---

## 1. 测试环节

### 1.1 `python -m pytest` 报 `No module named pytest` —— 新环境没装测试依赖

- **根因**：`src/requirements.txt` **不含 pytest**（它只声明运行时依赖），CI 是另外 `pip install -r src/requirements.txt pytest` 单独装的。`[静态·已核对]`
- **复现**：全新 venv → `pip install -r src/requirements.txt` → `python -m pytest tests`。
- **修复**：`pip install pytest`（若要和 CI 一致：`pip install -r src/requirements.txt pytest`）。
- **验证**：`python -m pytest --collect-only -q` 能列完 105 个 `test_*.py` 的用例，不再报 ModuleNotFoundError。

### 1.2 `ModuleNotFoundError: zhuzhu_Copilot` —— 不在项目根运行 / 没走 pytest.ini

- **根因**：`pytest.ini` 断言了两件事：`pythonpath = src`、`testpaths = tests`，`addopts = -q --tb=short`。这些都是**相对 rootdir** 解析的。`[静态·已核对]`
- **复现**：`cd tests` 后 `pytest`，或在别的目录下 `pytest "C:\...\tests"`。
- **修复**：一律在项目根执行 `python -m pytest tests`（`-m` 而非直接 `pytest`，保证用的是当前解释器）。
- **验证**：`python -m pytest -q` 顶部显示的 rootdir 为项目根、configfile 为 `pytest.ini`。

### 1.3 用例全绿但退出码非 0（`0xC0000005` 访问冲突 / `0xC0000409`）

- **根因**：部分用例 new 出**无父级**的 Qt 控件且不释放；解释器退出时 QApplication 先于这些 C++ 对象析构 → 进程崩溃退出。`tests/conftest.py` 文件头注释里记录了这条真实缺陷。`[静态·已核对]`
- **复现**：`python -m pytest tests -q; echo $LASTEXITCODE` —— 全程 `passed`，退出码非 0。
- **修复**：已在 `conftest.py` 里集中做会话级清理（`close + deleteLater + processEvents`），**不要在单支用例里再各写一遍**；新增 GUI 用例请给控件挂父级。
- **验证**：`$LASTEXITCODE` 为 0。**只看 "N passed" 不算验证通过。**

### 1.4 控制台大量中文时报 `UnicodeEncodeError: 'charmap' codec...`

- **根因**：Windows 控制台默认 cp1252/GBK 编码打不了部分中文。CI 用 `PYTHONIOENCODING: utf-8` 全局规避，注释里写明"曾把某个 --check 脚本整段打挂"。`[静态·已核对]`
- **复现**：GBK 代码页的 `cmd` 里直接跑那些会 `print` 中文的脚本（如 `scripts\verify_release_artifact.py`）。
- **修复**：`$env:PYTHONIOENCODING='utf-8'`（或 `chcp 65001`）。**只影响标准流编码，不改文件读写默认行为**，不要顺手加 `open(..., encoding=)` 去"修"。
- **验证**：同一脚本重跑无 traceback。

### 1.5 手工单跑某 GUI 用例时弹窗/卡死

- **根因**：`conftest.py` 用 `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` 把 GUI 测试拉到离屏渲染。绕开 conftest（例如 `python tests\test_xxx.py`）就拿不到这个环境。`[静态·已核对]`
- **复现**：`python tests\test_dock_layout.py`。
- **修复**：始终经 pytest 跑；临时手测请先 `$env:QT_QPA_PLATFORM='offscreen'`。
- **验证**：不弹窗、不卡住、进程正常退出。

### 1.6 先过语法闸，再谈逻辑

- **命令**：`python -m compileall -q src mcp_servers`（CI 同款第一步）。比 pytest 快一个量级，能先摘掉 `SyntaxError`/`IndentationError` 这类噪声。`[静态·已核对]`
- **静态**：`ruff check src mcp_servers --statistics`。注意 CI 对 ruff 是 `|| true`——**ruff 报错不阻断 CI，但别当没报**。

### 1.7 最小复现子集（定位期只跑这些）

```
python -m pytest tests/test_plugin_guard.py tests/test_plugin_source.py tests/test_quarantine.py -v   # 插件校验/回滚
python -m pytest tests/test_security_network.py tests/test_network_defense.py -v                     # 安全/网络防御
python -m pytest tests/test_retheme_refresh.py tests/test_preview_fallback_theme.py -v               # 主题刷新
python -m pytest tests/test_prompt_i18n.py -v                                                        # i18n 回退链
```
> 选中这几个文件是因为它们各对应本手册 §4 的一条运行期失败模式——**先有失败模式，再有子集**；不要反过来挑"跑得快"的。

---

## 2. 构建环节

### 2.1 构建"看起来跑完了"但失败 / 找不到任何日志

- **根因 A（判据）**：脚本的失败路径是 `Fail()` → `throw` → `catch { exit 1 }`，**退出码才是唯一判据**；控制台末行可能是 `Write-Head` 的成功横幅残留。`[静态·已核对]`
- **根因 B（日志缺失）**：日志路径 `build\build_yyyyMMdd_HHmmss.log` 由 `Start-Transcript` 写，而 `Start-Transcript` 失败是被 `try { } catch { }` **静默吞掉**的（`build_release.ps1` 收尾 `finally` 里才 `Stop-Transcript`）。`[静态·已核对]`
- **复现**：把 `build\` 设只读/被占用 → 构建照跑 → `build\` 下无 `.log`，且无任何提示。
- **修复**：确认 `build\` 可写（`build\` 不入库，若被清理或权限继承坏掉就重建目录）；定位期直接 `-NoLog` 关掉转录，让输出全在控制台，再手动 `Tee-Object`。
- **验证**：`build\build_*.log` 存在且内含 ISCC/pyinstaller 的原生输出；`$LASTEXITCODE` 为 0。

### 2.2 `-SkipRuntime` 之后 ISCC 报 `Source file "...\build\redist\vc_redist.x64.exe" does not exist`

- **根因**：`installer\zhuzhu_Copilot.iss` 的 `[Files]` 把 `..\build\redist\vc_redist.x64.exe` / `vc_redist.x86.exe` / `d3dcompiler_47.dll`、`..\build\certs\zhutianliang.cer` 都列为**编译期 Source**；而 `-SkipRuntime` **同时跳过了**「沙盒运行时打包」和「依赖组件准备（VC++/d3dcompiler）」两个阶段。干净机器上 `build\redist` 是空的 → ISCC 编译直接失败。`[静态·已核对]`
- **复现**：删掉 `build\redist\*` 后 `.\build_release.ps1 -SkipRuntime -NoSign`。
- **修复**：① 去掉 `-SkipRuntime` 完整跑一次补齐 `build\redist` 与 `build\certs`；② 或手工把两个 `vc_redist`、`d3dcompiler_47.dll` 与 `.cer` 放进对应目录后再 `-SkipRuntime`。
- **验证**：`Get-ChildItem build\redist, build\certs` 三件套 + 证书齐全；ISCC 阶段无 `Source file ... does not exist`。

### 2.3 构建在 PyInstaller 阶段被杀 / 无输出终止（8GB 内存）

- **根因**：`[环境·已知风险]` 8GB 内存 + PyQt6-WebEngine 打包属内存紧张场景，COLLECT 阶段是峰值。
- **复现**：同时开浏览器/IDE/微信后跑完整构建。
- **修复**：构建前关掉大户进程；不要并行跑两个构建；日志停在哪一行要记下来（停在 `Building COLLECT` 即坐实内存而非代码）。
- **验证**：同机空载重跑通过，且两次日志的控制台行数差异只在耗时。

### 2.4 `No code-signing certificate ... matching 'zhutianliang'`

- **根因**：私钥只在**证书库** `Cert:\CurrentUser\My`（不导出 .pfx），`scripts\sign_file.ps1` 以 `Subject -like *zhutianliang* -and HasPrivateKey` 查找。换账户/服务账户/别的机器构建 → 证书库为空。`[静态·已核对]`
- **复现**：用另一 Windows 账户跑 `.\build_release.ps1`。
- **修复**：用持证账户构建；内部调试用 `-NoSign` 出未签名包（UAC 会显示未知发布者，属预期）。
- **验证**：`Get-ChildItem Cert:\CurrentUser\My | ? { $_.Subject -like '*zhutianliang*' }` 有结果；构建尾部打印 `签名=Valid`。

### 2.5 安装包版本与主程序 FileVersion 对不上

- **根因**：`installer\zhuzhu_Copilot.iss` 的 `#define MyAppVersion` 是**硬编码**（当前 `7.0.1`），靠 `build_release.ps1` 的「版本号同步」阶段改写；绕过脚本直接调 ISCC 就不会同步。`[静态·已核对]`
- **复现**：改 `src` 里版本号后直接 `ISCC installer\zhuzhu_Copilot.iss`。
- **修复**：永远从脚本入口构建（README：`.\build_release.ps1` 一条命令；`installer\build_setup.bat` 是等价薄壳）。
- **验证**：`(Get-Item "dist\zhuzhu Copilot\zhuzhu Copilot.exe").VersionInfo.FileVersion` 与安装包属性页版本、`#define MyAppVersion` 三者一致。

### 2.6 构建流水线的正确顺序（别自己拼）

体检 → 运行时/种子 → 打包 → 签名 → Inno Setup → 产物自检。`-DryRun` 只体检不构建（改了一行代码想确认"环境有没有坏"时用它，最省时间）。产物自检是 `scripts\verify_release_artifact.py`——它不通过时**不要**手工补文件骗过检查，缺模块说明 spec 的 hiddenimport 漏了。

---

## 3. 安装 / 签名环节

### 3.1 卸载器报 `Messages file ... unins000.msg is missing`（退出码 0、什么都不删）

- **根因（已定性）**：Inno 在卸载器 EXE「已签名」时会把界面文本外置成 `unins000.msg`，而该文件**只在编译期启用 `SignedUninstaller` 时**由 Setup 生成。**事后补签** `unins000.exe` → 卸载器以为自己该读外部 msg → 文件不存在 → 当场中止。`[静态·已核对：iss 注释 + scripts\sign_file.ps1 头部说明]`
- **复现**：出未签名安装包 → 装完 → 手工签 `unins000.exe` → 卸载。
- **修复**：`#ifdef SIGNTOOL → SignedUninstaller=yes + SignTool={#SIGNTOOL}`，由 `build_release.ps1` 以 `ISCC -s<name>=...` 注册 SignTool，**编译期**签安装包与卸载器。**永远不要恢复"装完之后补签"的做法。**
- **验证**：`Get-AuthenticodeSignature "<安装目录>\unins000.exe"` 为 Valid，且同目录**存在** `unins000.msg`；实际执行一次卸载能删干净。

### 3.2 UAC 弹窗显示「未知发布者 / 签名无效」

- **根因**：UAC 的 consent.exe 在提升上下文里**只读 LocalMachine 信任存储**；`certutil` 默认装 `CurrentUser`。iss 的 `[Run]` 已显式 `-machine` 装 Root 与 TrustedPublisher，漏了这个参数就会复现。`[静态·已核对]`
- **复现**：把 `[Run]` 里的 `-machine` 去掉重装。
- **修复**：保持 `certutil -f -machine -addstore Root / TrustedPublisher` 两条。
- **验证**：`certutil -store -machine Root zhutianliang` 有输出；重装后 UAC 显示 zhutianliang。

### 3.3 安装时选了 English，装完界面仍是中文（或反之）

- **语言解析优先级**（`core/i18n.py::_installed_lang` 注释）：`--lang 参数 > 环境变量 ZHUZHU_LANG > 注册表设置 > app_lang.ini > 系统区域`。`[静态·已核对]`
- **根因**：`app_lang.ini` 是**最低优先级里的兜底**，它只在 `CurStepChanged(ssPostInstall)` 写入，且写失败**只 Log 不阻断**。所以"选英文仍中文"最常见的是**上一轮 QSettings 里存了 zh_CN**（注册表优先级更高），不是安装器坏了。`[静态·已核对]`
- **复现**：设置里手动改成中文 → 卸载重装选 English。
- **修复**：`HKCU\Software\zhuzhu_Copilot` 下的语言项清掉再启动；确认 `<安装目录>\app_lang.ini` 内容 `Lang=en_US`。
- **验证**：三级排除——先用 `--lang en_US` 强制（应生效，证明 i18n 与资源没问题），再清注册表，最后看 ini。

### 3.4 卸载后旧配置"复活" / 重装读到上一轮主题

- **根因定位点**：卸载清理只覆盖两处——`[Registry] uninsdeletekey` 清 `HKCU\Software\zhuzhu_Copilot`（及旧版 `HKCU\Software\WinAppMigrator`）、`CurUninstallStepChanged(usPostUninstall)` 删 `%USERPROFILE%\.zhuzhu_Copilot` 与 `.winapp_migrator`。`[静态·已核对]`
- **复现**：改主题 → 卸载 → 重装。
- **修复**：若残留，先确认卸载是走 `unins000.exe` 正常流程（非暴力删目录）；`RemoveUserDataDir` 走 `cmd /c rd /s /q`，**目录被占用时会静默失败**——卸载前确认主程序进程已结束（`[UninstallRun]` 已 `taskkill /F /IM`）。
- **验证**：卸载后 `Test-Path "$env:USERPROFILE\.zhuzhu_Copilot"` 为 False，注册表分支不存在。

---

## 4. 运行时环节

### 4.1 插件"自己消失了"

- **根因（设计如此，不是 bug）**：`core/plugin_guard.py` 判定 `danger` 会**强制整体删除插件目录**，并写事件到 `<数据目录>\plugins\.guard_log.jsonl`（`_guard_log_path() = agent_plugins.plugins_dir() / ".guard_log.jsonl"`；`plugins_dir()` = `app_identity.data_root()/plugins`，默认 `%USERPROFILE%\.zhuzhu_Copilot\plugins`）。`[静态·已核对]`
- **判定到 danger 的典型规则**（源码正则）：`registry`（`winreg.SetValue*`/`reg add`）、`mass_delete`（`shutil.rmtree`、`format X:`、`del /F`、`rm -rf /`）、凭据窃取/键盘记录、关闭系统防护、`guard_tamper`（`plugin_guard|plugin_source|copilot_plugins|security_engine|quarantine.py|yara_engine|overlay_hook|source_overlay` 等，仅 `.py`）。
- **复现**：写一个含 `winreg.SetValue` 的插件并应用 → 目录被整体移除。
- **修复**：改插件内容（用合法 API / 申请权限），**不要**改 `plugin_guard.py` 去放行（改了会命中 `guard_tamper` 自毁）；插件里的 `__pycache__`、`backups`、`applied.json` 被扫描跳过，别把代码藏那里。
- **验证**：`Get-Content "$env:USERPROFILE\.zhuzhu_Copilot\plugins\.guard_log.jsonl" -Tail 5`，看 `action=force_delete` / `force_delete_source_plugin`、`whole_plugin_removed`、以及命中的规则名与文件清单。**先读日志再争辩。**

### 4.2 源码级插件应用失败，但"看起来改了一半"

- **根因**：`core/plugin_source.py::apply_source_ops` 顺序是 结构校验 → **插件校验系统** → 逐文件备份 → 逐条应用 → **全部 `py_compile`** → 写 `applied.json`；**任一编译失败会整体回滚**（创建的删除、修改的还原）。`[静态·已核对]`
- **复现**：插件里两条 ops，第二条故意语法错 → 返回 not ok，第一条也已被还原。
- **修复**：按返回的 message 定位语法/结构问题；改插件后重试。revert 时**哈希与记录不符的文件会被跳过**（保护用户手工改动），所以"卸载后还留着一处改动"是预期的，不是漏删。`[静态·已核对]`
- **验证**：`applied.json` 的存在/内容与目标文件实际状态一致；`revert_source_ops` 后 diff 干净。

### 4.3 界面出现 `some.key.name` 或整段中文（本应英文）

- **根因**：`i18n.py::tr()` 的回退链是 **当前语言 → 中文(DEFAULT_LANG) → default → key**，**任何层级未命中都返回非空串**（设计理由：UI 空标题比显示 key 更糟）。`tp`（提示词）同理，`tpf` 占位符不匹配时**退回中文原文、不抛异常**。`[静态·已核对]`
- **要点**：`src\zhuzhu_Copilot\locales\` 下**只有 `en_US.json` 与 `prompt_en_US.json`**——中文是代码内默认，**别去找 `zh_CN.json`**；英文缺键就表现为"英文界面里蹦中文"。
- **复现**：临时在 `tr()` 里传一个不存在的键。
- **修复**：到 `locales\en_US.json` 的 `ui` 段补键（提示词补 `prompt_en_US.json` 的 `prompt` 段）；占位符必须与中文原文一致（`{name}` 命名占位符，`str.format` 注入）。
- **验证**：`python -m pytest tests/test_prompt_i18n.py -v`；界面不再出现键名或混语言。

### 4.4 主题切换后个别面板不刷新

- **定位入口**：`tests/test_retheme_refresh.py`、`tests/test_preview_fallback_theme.py` 就是为这两类现象写的回归闸。`[静态·已核对：用例文件存在及命名]`
- **复现/修复/验证**：先跑这两支用例看是否已能复现；能复现就走「改代码 → 同支用例转绿 → 再全量」。不能复现则记为新场景，补一支用例**再**改代码。

---

## 5. 现场速查（路径清单）

| 用途 | 路径 |
|---|---|
| 构建日志 | `build\build_yyyyMMdd_HHmmss.log`（`Start-Transcript`，失败会被静默吞） |
| 打包产物 | `dist\zhuzhu Copilot\`、`dist\zhuzhu Copilot Setup.exe` |
| 签名工具 | `scripts\sign_file.ps1`（Inno SignTool 编译期调用） |
| 产物自检 | `scripts\verify_release_artifact.py` |
| spec 生成 | `scripts\generate_spec.py` |
| 用户数据目录 | `%USERPROFILE%\.zhuzhu_Copilot`（= `app_identity.data_root()`） |
| 插件目录 | `%USERPROFILE%\.zhuzhu_Copilot\plugins` |
| 校验事件日志 | `%USERPROFILE%\.zhuzhu_Copilot\plugins\.guard_log.jsonl` |
| 旧版数据（迁移源/卸载兜底） | `%USERPROFILE%\.winapp_migrator` |
| 注册表设置 | `HKCU\Software\zhuzhu_Copilot`（旧：`HKCU\Software\WinAppMigrator`） |
| 安装期语言 | `<安装目录>\app_lang.ini` |
| 代码签名证书 | `Cert:\CurrentUser\My`（Subject 含 `zhutianliang`，私钥不导出） |

---

## 6. 命令速查

```powershell
# 测试
$env:PYTHONIOENCODING='utf-8'          # 中文输出防编码崩
python -m compileall -q src mcp_servers
ruff check src mcp_servers --statistics
python -m pytest tests -v              # 新环境先 pip install pytest

# 构建
.\build_release.ps1 -DryRun                            # 只体检
.\build_release.ps1 -SkipRuntime -SkipSeed -NoSign     # 内部快速迭代（须已有 build\redist、build\certs）
.\build_release.ps1                                    # 正式出包

# 现场取证
Get-AuthenticodeSignature "dist\zhuzhu Copilot Setup.exe"
Get-Content build\build_*.log -Tail 40
Get-Content "$env:USERPROFILE\.zhuzhu_Copilot\plugins\.guard_log.jsonl" -Tail 10
Get-ChildItem Cert:\CurrentUser\My | ? { $_.Subject -like '*zhutianliang*' }
```

---

## 7. 未实测清单（到现场先跑这 5 条，跑完把本手册的 [静态] 升级成 [已实测]）

1. `python -m pytest tests -q` 全量是否真绿 **且 `$LASTEXITCODE -eq 0`**（§1.3）。
2. 干净 `build\redist` 下 `.\build_release.ps1 -SkipRuntime -NoSign` 是否按 §2.2 报 ISCC Source 缺失。
3. `.\build_release.ps1 -DryRun` 的退出码与体检项清单。
4. 卸载后 `unins000.msg` 是否存在、卸载是否真的删干净（§3.1/§3.4）。
5. `-NoSign` 包安装后 UAC 显示（预期未知发布者）与主程序能否正常启动。

> 复核约定：本手册只写「根因 / 复现 / 修复 / 验证」四段，不收录"应该是""大概是"类结论；未核实项一律留在 §7，不往下沉。
