# 安全防护引擎增强 · 子项目 A：端点防护

日期：2026-09-05

> 范围：本次设计仅覆盖 4 个子项目中的 **A（端点防护增强）**。
> 总体路线 A→B→C→D 已与用户确认：A 端点防护 → B 广告弹窗拦截 → C 网络防护升级 → D Windows 安全中心集成。
> 每阶段独立设计 → 开发 → Test and Debug 验证 → 提交 git。全部配置外置，零硬编码。

## 1. 设计决策记录（已与用户逐项确认）

| 决策点    | 结论                                                   |
| ------ | ---------------------------------------------------- |
| 交付顺序   | 分阶段全做，按 A→B→C→D 推进                                   |
| 识别引擎   | 规则引擎 + 可选 LLM 深度分析（LLM 默认关闭，不可用时自动降级）                |
| 处置策略   | 分级自动处置（高置信度自动拦截隔离，低置信度仅提示/记录）                        |
| 勒索防护深度 | 实时行为监控（ReadDirectoryChangesW）+ 溯源处置（终止肇事件、隔离已改文件可恢复） |
| 监控架构   | 方案 2：纯用户态 Python 事件驱动 + 叠加原生 AMSI Provider DLL       |
| 测试要求   | 完成每项功能必须 Test and Debug，真实场景非 mock                   |

## 2. 总体架构

新建目录 `src/zhuzhu_Copilot/core/security_engine/`，作为统一防护引擎。
现有 `core/security.py`（进程/启动项扫描、签名校验、隔离区）与 `core/execution_guard.py`（格机/无文件攻击拦截）保留，作为底层工具被新引擎复用。

```
security_engine/
├── engine.py            # 统一调度：常驻线程组、事件流、处置决策、审计
├── rules.py             # 规则引擎：加载外置规则库 rules.json，返回 (命中规则, 置信度)；支持 add_detector(name, fn) 注册式扩展
├── file_intel.py        # 文件静态分析：SHA256 / PE导入表 / 信息熵 / 可疑字符串 / 加壳特征
├── script_intel.py      # 脚本分析：PowerShell/Cmd/HTA/VBA 危险模式、混淆度
├── command_intel.py     # 命令监控：进程创建事件 + PEB 命令行 → 规则匹配（复用 execution_guard 基础）
├── llm_analyzer.py      # 可选 LLM 深度分析：低置信度灰色区间样本升级，复用应用已配置模型连接
├── amsi_bridge.py       # AMSI Provider 对接：named pipe IPC，接收原生 DLL 投递的脚本内容
├── ransomware_shield.py # 勒索防护盾：ReadDirectoryChangesW 实时监控 + 行为检测 + 溯源处置
└── startup_guard.py     # 启动项实时防护：RegNotifyChangeKeyValue + 启动文件夹监听
```

配置全部外置：`config/security_rules.json`（规则库与处置策略），设置项存 QSettings（开关、受保护目录、处置级别、LLM 开关）。

## 3. 检测引擎

### 3.1 规则库（外置 JSON，零硬编码）

每条规则：

```json
{
  "id": "rule_xxx",
  "category": "file|script|command|startup|behavior",
  "confidence": 80,
  "matchers": [ { "type": "...", "params": {...} } ],
  "suggested_action": "quarantine|terminate|notify|log"
}
```

匹配器类型（可扩展注册）：

* `hash`：SHA256 哈希匹配（防改名的已知恶意文件）

* `pattern`：字符串/字节模式（可正则，大小写不敏感）

* `pe-import`：PE 导入表函数/模块特征

* `entropy`：高熵阈值

* `path-regex`：可疑路径模式

* `cmdline-regex`：命令行模式（进程/脚本命令检测）

* `extension`：扩展名列表（勒索扩展名等）

`action_policy` 映射置信度 → 动作，同样外置可配置。

### 3.2 文件静态分析 `file_intel`

* PE 头解析（节区表 / 导入表 / 资源段）：ctypes 直接读文件，真实 API

* 信息熵计算；可疑字符串检测（反调试 / VM 检测 / 自删除特征）

* 加壳特征（节区名、区段熵、入口点特征）

* 返回分析摘要供规则引擎匹配与 LLM 判定

### 3.3 脚本分析 `script_intel`

* 危险 cmdlet / 混淆检测：`-enc`、`IEX(...)`、`DownloadString`、`Invoke-Expression`、base64 高占比、多层编码嵌套

* 规则以正则外置到规则库，新增检测模式无需改代码

### 3.4 命令监控 `command_intel`

* WMI `Win32_ProcessStartTrace` 订阅进程创建事件（真实 API），读取目标进程 PEB 命令行（复用 security.\_process\_path / execution\_guard 基础）

* 现有 execution\_guard 的格机/无文件攻击拦截逻辑并入统一事件流，保持其"仅脚本解释器命中才自动终止"的保守策略

### 3.5 LLM 深度分析 `llm_analyzer`（默认关闭）

* 触发条件：规则引擎置信度落在灰色区间（默认 40–70，可配置）

* 输入：样本摘要（PE 导入表 / 脚本前 N 字符 / 行为轨迹）

* 输出：置信度与判定理由；LLM 不可用 / 超时 → 自动退回规则判定，不阻断

## 4. AMSI Provider 原生 DLL

### 4.1 组件

* 源码位置：`build/amsi_provider/`（C 语言，约 300–400 行）

* 构建期编译为 `amsi_provider.dll`：优先复用项目现有 Cython 编译链；本机无 C 编译器时构建脚本自动下载便携 MinGW（沿用沙盒运行时自动下载的既有模式）

* 随包打包（zhuzhu_Copilot.spec data 项），并走现有 zhutianliang 证书签名

### 4.2 职责

* 实现标准 AMSI Provider 接口（`DllGetClassObject` + `IAntimalwareProvider`），注册到 `HKLM\SOFTWARE\Microsoft\AMSI\Providers\{GUID}` 与 CLSID `InprocServer32`

* 宿主（PowerShell 等）发起 AMSI 扫描时，脚本内容经 **named pipe** 投递主程序防护引擎判定，返回 `AMSI_RESULT`（DETECTED / NOT\_DETECTED）

* 判定为恶意 → 返回 `AMSI_RESULT_DETECTED`，宿主终止脚本执行

### 4.3 性能与安全约束（微软对 AMSI Provider 的强制要求）

* 扫描必须快速返回：管道等主程序判定设超时（默认 1 秒，可配置），超时返回"未检测"，绝不卡死脚本执行

* 主程序引擎不在线 → 降级"未检测"，不干扰系统

* DLL 内不进行实质判定逻辑，只做投递与结果回传

### 4.4 注册/反注册

* 管理员权限下随防护开关开启时写注册表（HKLM AMSI Providers + CLSID）

* 关闭/卸载时调用 `DllUnregisterServer` 逻辑反注册，不残留

## 5. 勒索防护盾 `ransomware_shield`

### 5.1 实时监控

* `ReadDirectoryChangesW` 递归监控受保护目录：默认 桌面/文档/图片/下载（用户可增删，存 QSettings）

### 5.2 行为检测（多信号加权，阈值外置可配置）

* 短时间窗口内批量改名 / 批量新增高熵文件

* 文件扩展名被改为已知勒索扩展（列表外置）+ 文件头被改写为加密特征

* 单进程写入速率异常飙升 + 文件熵显著升高

### 5.3 溯源与处置（分级自动处置）

* 定位肇事件：行为记录关联 / `GetFileInformationByHandleEx` 反查文件句柄归属 PID

* 终止肇事件 → 已改写文件先备份到隔离区再处置

* 高置信度自动处置；中低置信度悬浮提示用户确认

### 5.4 白名单

* 本应用自身目录、系统目录、用户配置的信任目录免检，避免误拦正常迁移/编辑

* 本应用的迁移/文件操作进程自动加入白名单

## 6. 启动项防护 `startup_guard`

### 6.1 实时监听（不再仅靠定期扫描）

* `RegNotifyChangeKeyValue` 监听：

  * `HKLM\Software\Microsoft\Windows\CurrentVersion\Run` / `RunOnce`

  * `HKCU\...\Run` / `RunOnce`、`RunServices`、`StartupApproved`

* 启动文件夹（当前用户 + 公共用户）用 `ReadDirectoryChangesW` 监听

### 6.2 判定与处置

* 新增启动项 → 规则引擎（"startup" 类别规则）→ 分级处置：

  * 高置信度：隔离区备份 → 删除（复用 security.py 隔离区机制）

  * 低置信度：仅提示

* 可信来源自动放行：已签名且签发者位于可信厂商白名单（如微软/Adobe，外置配置）的启动项不打扰用户

## 7. 处置决策与隔离区

* 置信度分级 → 动作（`action_policy` 外置，无硬编码）：

  * 高置信度 ≥80：自动隔离/终止 + 审计日志 + 托盘气泡提示

  * 中置信度 40–79：隔离区预览通知，用户一键确认或恢复

  * 低置信度 <40：仅记录 + 静默忽略

* 隔离区复用现有 `security.py` 机制，扩展支持大文件（先压缩再隔离）与目录型隔离

* 审计：所有处置写 `隔离区/audit/` JSON（时间戳 + 命中规则 id），与现有进程审计格式一致

## 8. 配置与 UI 集成

* 设置页新增「安全防护」区（沿用 App 简约淡灰风格：纯黑+淡灰+白+深蓝、矢量图标、无 emoji）：

  * 受保护目录列表（增删，默认桌面/文档/图片/下载）

  * 处置级别（全部自动 / 分级 / 全部确认）

  * LLM 深度分析开关（复用现有模型连接配置）

  * AMSI Provider 注册/反注册状态与开关

  * 规则库查看与重载（JSON 改动热加载生效）

* 主窗口：现有 `SecurityMonitorWorker` 扩展接入新引擎各守护线程，统一结果信号汇总到托盘/主窗口提示

## 9. 测试与调试（每项功能必测）

沿用 `scripts/_verify_*.py` 惯例，真实场景非 mock：

* `_verify_rules_engine.py`：含已知恶意哈希/模式的样本命中与置信度；负样本误报验证

* `_verify_file_intel.py`：PE 导入表 / 熵 / 字符串 / 加壳特征样本验证

* `_verify_script_intel.py`：混淆 PowerShell / 下载执行样本验证

* `_verify_llm_analyzer.py`：灰色区间样本升级判定 + LLM 掉线降级验证

* `_verify_ransomware_shield.py`：模拟批量改名 / 高熵写入，验证检测→溯源→隔离→恢复全链路

* `_verify_startup_guard.py`：临时写入启动项，验证实时发现→规则判定→隔离

* `_verify_amsi_dll.py`：注册 DLL → PowerShell 触发 AMSI 扫描 → 管道判定与超时降级 → 反注册

* `_verify_engine.py`：整体事件流 + 配置热加载 + 并发稳定性

* 每轮修改后运行 `scripts/smoke_test.py` 回归

## 10. 构建集成

* `zhuzhu_Copilot.spec`：加入 `security_engine/` 模块 + `amsi_provider.dll` 数据文件

* 构建期 Cython 全模块编译流程自动覆盖新目录；AMSI DLL 无编译器时自动下载便携 MinGW 构建

* `build_sign.ps1` 扩展：新增 DLL 同样走 zhutianliang 证书签名

## 11. 后续阶段（本设计不涉及，仅记录）

* **B 广告弹窗拦截**：窗口枚举 + 广告特征库识别流氓弹窗并静默关闭

* **C 网络防护升级**：DDoS（SYN/ICMP/DNS 反射）、CC（本机服务按源限速封禁）、流量劫持（DNS/代理/网关 ARP 检测）；XSS 仅对内置 WebView 与自家 update-server 防护

* **D Windows 安全中心集成**：注册安全软件 Provider（AV/防火墙/反间谍）+ AMSI 提供方；"在 WSC 内设置防护参数"现实不可行，参数在本应用内设置

