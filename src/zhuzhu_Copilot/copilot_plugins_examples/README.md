# Copilot 插件开发指南

本目录是 Copilot 插件的官方示例。插件包是普通 zip，导入后由应用校验、写入**转译层**、
启用时自动生效（必要时热加载），不直接改写应用源码文件。

- 插件安装位置：`~/.zhuzhu_Copilot/copilot_plugins/<插件名>/`
- 变更落地点：`~/.zhuzhu_Copilot/source_overlay/`（转译层，镜像 `zhuzhu_Copilot/...` 相对路径）
- 生效方式：启用时自动写入并热加载；停用时自动回滚

---

## 0. 最小插件包

一个**纯运行时扩展**（不做任何源码变更）的最小包只有两个文件：

```
my-plugin/
├── plugin.json       # 必需：元数据
└── copilot.json      # 可选：页面 / 服务 / 托盘 / 快捷键
```

`plugin.json`：

```json
{
  "name": "my-plugin",
  "kind": "copilot",
  "author": "你的名字或公司",
  "description": "一句话说明这个插件做什么",
  "enabled": true,
  "created_at": "2026-10-25 10:00:00",
  "updated_at": "2026-10-25 10:00:00",
  "icon": ""
}
```

| 字段 | 说明 |
| --- | --- |
| `name` | 插件标识（字母/数字/`_`/`-`），同时是目录名与唯一键 |
| `kind` | Copilot 插件固定 `copilot` |
| `author` | 开发者/公司，插件页展示 |
| `icon` | 包内图标文件名（如 `icon.png`），可留空 |
| `enabled` | 是否启用；启用时自动应用转译层，停用时自动回滚 |

打包与导入：

```bash
# 在 my-plugin 目录的上一层执行，zip 根目录必须直接是 plugin.json
python -c "import zipfile,pathlib;z=zipfile.ZipFile('my-plugin.zip','w');[z.write(p,pathlib.Path(p).relative_to('my-plugin')) for p in pathlib.Path('my-plugin').rglob('*') if p.is_file()];z.close()"
```

导入入口（任选）：设置 → Copilot 插件 → 导入插件包；或把 zip 拖进对话输入框。

---

## 1. 三种源码级开发方法

源码级插件用一个 `source_ops.json` 描述「对谁、做什么改动」：

```
my-plugin/
├── plugin.json
├── source_ops.json   # 源码变更清单
└── source/           # 可选：变更内容的可读副本，便于 review（不参与执行）
```

三条路可以任选一条，也可以组合（见第 2 节）。

### 1.1 方法一：新建源码文件（新功能模块）

适用：功能是独立的，不需要改动现有实现。新文件就是一个普通 Python 模块，
写好后由其他 op（或运行时）调用。

```json
[
  {
    "op": "create_file",
    "path": "zhuzhu_Copilot/core/tool_hello_demo.py",
    "content": "\"\"\"自定义工具模块\"\"\"\n\n\ndef run(args: dict) -> dict:\n    name = str((args or {}).get(\"name\") or \"世界\").strip() or \"世界\"\n    return {\"text\": f\"你好，{name}！\", \"images\": []}\n"
  }
]
```

要点：

- `path` 相对源码根（`src/`），只允许 `zhuzhu_Copilot/...` 下的相对路径；
- 目标已存在会被拒绝（需整体覆盖请用 `replace_file`）；
- 新建的模块在启用后可直接 `import`（转译层已就位）。

完整示例：[custom-hello-tool](custom-hello-tool/source_ops.json) 的第 1 条 op。

### 1.2 方法二：插入源码到现有文件（不改原行）

适用：登记工具 schema、接线分发器、追加包装函数、注册导航项等「加一段」的场景。
**不修改任何已有代码行，风险最低**。

两种定位方式：

- **文本锚点**（`anchor` / `start`）：必须在该文件中真实存在且唯一；
- **行号**（`line` / `start_line`）：1 起，适合你能拿到完整源码（如 GitHub 仓库）时。

```json
[
  {
    "op": "insert_after",
    "path": "zhuzhu_Copilot/core/agent_tools.py",
    "anchor": "TOOLS = [",
    "content": "    {\n        \"type\": \"function\",\n        \"function\": {\n            \"name\": \"hello_demo\",\n            \"description\": \"向指定名字问好\",\n            \"parameters\": {\"type\": \"object\",\n                           \"properties\": {\"name\": {\"type\": \"string\"}},\n                           \"required\": []}\n        },\n    },"
  },
  {
    "op": "insert_after",
    "path": "zhuzhu_Copilot/core/agent_tools.py",
    "anchor": "            return _revert_copilot_plugin(str(args.get(\"name\", \"\")))",
    "content": "        # 分发接线\n        if name == \"hello_demo\":\n            from zhuzhu_Copilot.core import tool_hello_demo\n            return tool_hello_demo.run(args)"
  }
]
```

四个「插入类」op：

| op | 行为 | 必需字段 |
| --- | --- | --- |
| `insert_after` | 在锚点行（或指定行）**之后**插入，并自动补一个空行 | `path` `content` + `anchor` 或 `line` |
| `insert_at` | 在**第 N 行之前**插入（`N = 行数+1` 等价追加到末尾） | `path` `line` `content` |
| `append` | 追加到文件末尾，或 `after` 指定行之后 | `path` `content` + 可选 `after` |
| `replace_lines` | 用新内容替换第 `start_line..end_line` 行（含边界） | `path` `start_line` `content` + 可选 `end_line` |

要点：

- 锚点要**唯一**：先 `read_file` 看真实内容，取一行足够特征的字符串；
- 多条 op 改同一文件时，行号会随前一条插入而偏移，建议**从后往前**写行号，或全程用锚点；
- 插入位置要落在正确的缩进层级（例如插进函数体就带 4 空格缩进）。

完整示例：[custom-hello-tool](custom-hello-tool/source_ops.json)、[py-syntax-lint](py-syntax-lint/source_ops.json)、
[theme-deep-blue](theme-deep-blue/source_ops.json)（插到 `set_palette` 函数体内，保证每次切主题都生效）。

### 1.3 方法三：修改/替换现有源码

适用：必须改造既有行为，或整段实现需要重写。

```json
[
  {
    "op": "replace_module",
    "path": "zhuzhu_Copilot/core/agent_skills.py",
    "start": "    return parts",
    "end": "    return parts",
    "content": "    from zhuzhu_Copilot.core.prompt_optimizer import PROMPT_DISCIPLINE\n    parts.append(PROMPT_DISCIPLINE)\n    return parts"
  }
]
```

两个「替换类」op：

| op | 行为 | 必需字段 |
| --- | --- | --- |
| `replace_module` | 替换 `start..end` 之间的整段（`end` 省略则替换到文件末尾） | `path` `content` + `start` 或 `start_line` |
| `replace_file` | 整体覆盖文件 | `path` `content` |

要点：

- 替换后的代码必须**完整可编译**（写入后逐文件 `py_compile`，失败整体回滚）；
- `replace_module` 的 `start` 也要求唯一，避免替错位置；
- `replace_file` 风险最高：原文件后续升级会被你这份快照覆盖，尽量少用。

完整示例：[prompt-optimizer](prompt-optimizer/source_ops.json)。

### 1.4 三种方法怎么选

| 场景 | 选择 |
| --- | --- |
| 纯新增功能、不碰旧逻辑 | 方法一（`create_file`） |
| 给现有流程加一步（登记/接线/包装） | 方法二（插入） |
| 必须改已有行为 | 方法三（替换） |
| 大功能 | 方法一建模块 + 方法二接线（最常见组合） |

---

## 2. 综合性插件（三种形式 + 应用级扩展）

综合插件 = 多条 op 跨多个文件 + 可选的 `copilot.json` 运行时扩展 + 可选 `agent/` 子包。

```
my-plugin/
├── plugin.json
├── source_ops.json     # 三种形式混用，一次生效
├── copilot.json        # 可选：页面/服务/托盘/快捷键
├── pages/…             # 随包分发的扩展模块
├── agent/              # 可选：SKILL.md + server.py（登记为技能 / MCP）
└── icon.png            # 可选
```

### 2.1 组合示例：新增页面并注册进设置

一条插件同时做三件事：新建页面模块（方法一）→ 插入导航项（方法二）→ 追加页面构建方法（方法二）。

```json
[
  {"op": "create_file", "path": "zhuzhu_Copilot/ui/page_quote.py",
   "content": "from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel\n\n\nclass QuotePage(QWidget):\n    def __init__(self, parent=None):\n        super().__init__(parent)\n        lay = QVBoxLayout(self)\n        lay.addWidget(QLabel(\"每日一句\"))\n"},
  {"op": "insert_after", "path": "zhuzhu_Copilot/ui/agent_panel.py",
   "anchor": "    (\"Copilot 插件\", \"copilot_plugins\"),", "content": "    (\"每日一句\", \"quote\"),"},
  {"op": "insert_after", "path": "zhuzhu_Copilot/ui/agent_panel.py",
   "anchor": "            self._build_copilot_plugin_page,", "content": "            self._build_quote_page,"},
  {"op": "append", "path": "zhuzhu_Copilot/ui/agent_panel.py",
   "after": "    # ---- Copilot 插件扩展点：插件可在此之后追加页面构建方法 ----",
   "content": "\n    def _build_quote_page(self) -> QWidget:\n        from zhuzhu_Copilot.ui.page_quote import QuotePage\n        return QuotePage(self.stack)"}
]
```

完整示例：[settings-page-demo](settings-page-demo/source_ops.json)。

### 2.2 组合示例：新模块 + 替换 + 接线

[py-syntax-lint](py-syntax-lint/source_ops.json)：
新建高亮模块 → 在 `agent_tools.py` 登记 `check_python_syntax` 工具并接线 →
在 `agent_panel.py` 末尾追加包装函数重绑定 `_md_to_html`（原文件一行未改）。

[prompt-optimizer](prompt-optimizer/source_ops.json)：
新建优化模块 → `replace_module` 改造 `agent_skills._base_prompt_parts` → 登记 `optimize_prompt` 工具。

### 2.3 组合示例：纯运行时（不写源码也能做强功能）

[agent-scorecard](agent-scorecard/) 不做任何源码变更，只声明一个服务：

```json
{
  "services": [
    {"id": "agent-scorecard", "title": "任务评分窗",
     "module": "service/scorecard_service.py",
     "entry": "start", "stop": "stop"}
  ]
}
```

服务在 `start()` 里运行时包装 `agent_tools.execute_tool` / `AgentEngine.run`，
任务结束弹评分窗；`stop()` 完整还原。这种方式**启用即生效**，对应用源码零侵入。

### 2.4 `copilot.json` 字段

| section | 字段 | 说明 |
| --- | --- | --- |
| `pages` | `id` `title` `module` `widget` | `widget` 为模块内的页面类名；追加为 Copilot 浮层页签 |
| `services` | `id` `title` `module` `entry` `stop` | `entry()` 在 GUI 线程调用；`stop()` 或返回句柄的 `stop/close` 在停用/退出时调用 |
| `tray` | `id` `title` `module` `action` | `action()` 追加到托盘菜单 |
| `shortcuts` | `id` `title` `module` `action` `hotkey` | `hotkey` 形如 `ctrl+alt+m`，注册为 `QShortcut` |

约定：

- `id` 全局唯一（跨插件重复会被拒绝）；
- `module` 是**包内相对路径**（随包分发，不走转译层）；
- 服务会被注入 `config.json` 的键值（键名自动大写为环境变量），可用于 MCP/API 配置。

---

## 3. 完整 op 参考

```json
{"op": "create_file",    "path": "zhuzhu_Copilot/core/x.py", "content": "完整文件内容"}
{"op": "replace_file",   "path": "zhuzhu_Copilot/core/x.py", "content": "完整文件内容"}
{"op": "append",         "path": "zhuzhu_Copilot/core/x.py", "content": "…", "after": "锚点行(可选)"}
{"op": "insert_after",   "path": "zhuzhu_Copilot/core/x.py", "anchor": "唯一锚点", "content": "…"}
{"op": "insert_after",   "path": "zhuzhu_Copilot/core/x.py", "line": 120, "content": "…"}
{"op": "insert_at",      "path": "zhuzhu_Copilot/core/x.py", "line": 120, "content": "插到第 120 行之前"}
{"op": "replace_lines",  "path": "zhuzhu_Copilot/core/x.py", "start_line": 100, "end_line": 120, "content": "…"}
{"op": "replace_module", "path": "zhuzhu_Copilot/core/x.py", "start": "起始锚点", "end": "结束锚点(可选)", "content": "…"}
{"op": "replace_module", "path": "zhuzhu_Copilot/core/x.py", "start_line": 100, "end_line": 120, "content": "…"}
```

约束：

- `path` 必须是相对路径，禁止绝对路径与 `..` 穿越；
- 单插件最多 50 条 op、单条内容最多 512 KB；
- 受保护核心程序不可改动：`plugin_guard.py`、`security_engine/`、`plugin_source.py`、
  `copilot_plugins.py`、`_copilot_overlay_hook.py`、`main.py` 等；
- 写回时保持原文件换行风格（CRLF 文件不会整篇变 LF）。

---

## 4. 生命周期

```
导入 → 校验（静态规则 + LLM 源码评审）→ 落盘到 copilot_plugins/<name>/
启用 → 自动写入转译层（逐文件备份 + py_compile，失败整体回滚）
     → 热加载受影响模块 → 启动 copilot.json 声明的服务
停用 → 停止服务 → 按备份自动回滚转译层 → 热加载回落原版
删除 → 自动回滚 → 删除插件目录
```

- 启用失败（校验不通过 / 锚点找不到 / 编译失败）会**整体回滚**且保持停用，不会留下半套变更；
- 热加载跳过包根与持有进程级状态的模块（`agent_panel`、`main_window`、`styles` 等），
  这类变更在下次启动时生效，插件页会提示；
- 新会话 / 新打开的设置窗会使用新代码；已存在的实例保持旧实现（Python 语义）。

---

## 5. 调试与排错

插件页卡片上的状态就是第一手诊断信息：

| 卡片状态 | 含义 |
| --- | --- |
| `服务运行中` | `copilot.json` 的服务已在本进程启动 |
| `服务未运行` | 服务条目存在但没启动（通常是刚改过、需要重新启用） |
| `服务加载失败` | 服务抛异常，修复后重新启用即可 |
| `已写入转译层` | `source_ops` 已生效 |
| `未写入转译层` | 无 `source_ops`，或写入失败 |

常见失败与处理：

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 启用失败：找不到锚点 | 目标文件版本不一致 | 用 `read_file` 重新取锚点，或改用行号 |
| 启用失败：锚点不唯一 | 锚点串在文件里出现多次 | 加长锚点，包含更多上下文 |
| 启用失败：编译检查未通过 | 插入/替换后的代码不完整 | 检查缩进与括号；`replace_module` 的 `end` 是否漏了 |
| 插件在但没效果 | 热加载失败被自动回滚（摘要里有 failed） | 查看热加载摘要/校验日志，停用再启用一次；不需要重启 |
| 插件被删除 | 校验系统判定危险代码 | 查看校验日志 `copilot_plugins/.guard_log.jsonl` |

设计阶段建议先 `read_file` 目标文件确认锚点，再用「创建 Copilot 插件（自然语言）」
生成草稿，最后用「详情/配置」里的「源码变更」页签逐条 review。

---

## 6. 示例索引

| 示例 | 演示能力 | 用到的 op |
| --- | --- | --- |
| `theme-deep-blue` | 修改主题：强调色换深蓝，切主题后仍生效 | `insert_after`（插进 `set_palette` 函数体） |
| `custom-hello-tool` | 新增 `hello_demo` 工具：模块 + schema + 分发接线 | `create_file` + 2×`insert_after` |
| `settings-page-demo` | 自定义设置页：页面模块 + 导航 + 构建器 + 构建方法 | `create_file` + 2×`insert_after` + `append` |
| `py-syntax-lint` | 代码高亮 + `check_python_syntax` 工具（只解析不执行） | `create_file` + 3×`insert_after` |
| `prompt-optimizer` | 提示词优化工具 + 改写系统提示词 | `create_file` + `replace_module` + 2×`insert_after` |
| `perf-turbo` | 性能优化：渲染缓存 + 流式节拍自适应 + 性能看板 + `perf_report` 工具 | `create_file` + `replace_module` + 3×`insert_after` + `copilot.json` |
| `code-map` | 工作区代码地图：扫描内核 + `code_map` 工具 + 看板页 + 预热服务 + 技能 + **自定义图标** | `create_file` + 2×`insert_after` + `copilot.json`（四项齐全）+ `agent/` |
| `tray-quick-note` | 托盘动作 + 后台服务（纯运行时） | 无源码变更，靠 `copilot.json` |
| `agent-scorecard` | 任务评分窗：耗时 / 工具 / token + 评分回传（纯运行时） | 无源码变更，靠 `copilot.json` 服务 + 运行时挂钩 |

> 「插入」与「替换」是两条不同的路：
> **插入**不改动任何已有代码行，风险最低，适合加能力、包装函数、接线；
> **替换**直接改写已有实现，用于改造既有行为，需自己保证替换后的代码完整可编译。
>
> `py-syntax-lint`、`prompt-optimizer` 的 `source/` 目录是变更内容的**可读副本**，
> 插件实际执行的是 `source_ops.json` 里的 `content`，两者内容应保持一致。

---

## 7. 图标（icon）

三件事缺一就会「图标不显示」：

1. 图标文件放在**包根**（与 `plugin.json` 同目录），**不能放子目录**；
2. `plugin.json` 写 `"icon": "icon.png"`——**只写文件名**，不能带 `pages/` 之类前缀，
   也不能以 `.` 开头（安全校验判非法后会清空该字段）；
3. 扩展名在白名单：`.png` `.jpg` `.jpeg` `.svg` `.ico` `.gif` `.webp`。**推荐 PNG**
   （exe 安装版对 PNG 最稳），方形 128~256px，卡片按 44px 等比缩放。

示例：`code-map/icon.png` 是 256×256 纯标准库光栅化生成的图标（4× 超采样抗锯齿），
`plugin.json` 里 `"icon": "icon.png"`。

## 8. 热插拔与液体玻璃窗口

**热插拔（2026-10-25 起支持）**：页面页签、玻璃窗口、托盘动作、快捷键都是可撤销登记。
停用/删除插件时立即从界面摘掉（`main_window.unregister_copilot_plugin`），启用时当场挂上
（`start_copilot_plugin`，面板缺省用启动时登记的宿主）；后台服务同样即时启停。
**源码级变更（含 `agent_panel` / `main_window` / `styles`）由热加载引擎在运行进程内
即时装载（活类热修补 + 活实例刷新 + UI 事件桥），任何模块都不需要重启应用**；
热加载失败会自动回滚，不会留下半套变更。
回归用例：`tests/test_copilot_hotplug.py`、`tests/test_hot_reload_engine.py`、
`tests/test_copilot_hotplug_lifecycle.py`。

**液体玻璃窗口**：`pages` 条目加 `"window": "glass"` 即把页面托管到独立无框玻璃窗口
（GPU 实时折射/反射/边缘高光，见根 README 第八章 8.8）：

```json
{"id": "my-page", "title": "我的面板", "module": "pages/x.py", "widget": "MyPage",
 "window": "glass", "auto_show": true, "width": 720, "height": 560}
```

`auto_show=true` 时应用启动直接浮现（常驻）；`position: [x, y]` 可固定位置，不给则居中
且多个窗口自动错位。`GLASS_MODE=painter` 可强制降级排障。观感参考 `perf-turbo` /
`code-map` 两个示例。

## 9. 在 exe（安装版）里验证转译层

安装目录没有源码，源码级插件靠三样东西工作，缺一样就会「装上了却没反应」：

1. 随包源码快照 `_copilot_src/zhuzhu_Copilot/**`（插入/替换类 op 的原版基准）；
2. 用户数据目录的 `source_overlay/`（变更的实际生效副本）；
3. `_copilot_overlay_hook.py` 导入钩子（让 `zhuzhu_Copilot.*` 优先命中覆盖层）。

**先体检**（不依赖任何插件，随时可跑）：

```powershell
python scripts/verify_overlay.py                 # 打包态模拟 + 体检 dist/zhuzhu Copilot
python scripts/verify_overlay.py --simulate      # 只模拟（临时目录，不碰你的数据）
python scripts/verify_overlay.py --inspect "D:\Program Files\zhuzhu Copilot"
```

若体检报「缺少 `_copilot_src/zhuzhu_Copilot`」，说明该构建**不含**覆盖层机制
（例如仓库里 2026-10-19 那版 `dist/`），必须 `.\build_release.ps1` 重新打包。

**再验证**：装一个看得见的插件（`code-map` 最合适）→ 启用（无需重启，运行进程内即时生效），
然后看三个信号：

| 信号 | 说明 |
| --- | --- |
| 浮层多出「代码地图」页签 | `copilot.json` 的应用扩展生效 |
| AI 能调 `code_map` 工具 | 源码级 `insert_after` 生效（工具被登记进 TOOLS） |
| 插件页顶部「生效位置」有模块数、卡片显示「已写入转译层」 | 转译层机制自报状态 |

反证：停用插件 → 页签立即消失、工具不可用、模块数归零（同样无需重启）。

> 详细的手把手教程（含锚点取法、行号 vs 锚点、排错表、让 AI 帮你写插件）见仓库根目录
> `README.md` 第八章「插件开发指南」。
