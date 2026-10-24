# Copilot 插件示例（供开发者学习）

六个示例覆盖 Copilot 插件的全部能力形态（含源码级「插入」与「替换」两条路）。**安装任示例 = 导入其目录打成的 zip**：

```bash
# 在设置 → Copilot 插件页点「导入插件包」，选择对应 zip
# 或命令行（任选其一）：
python -c "import zipfile,pathlib;
z=zipfile.ZipFile('theme-deep-blue.zip','w');
[z.write(p, pathlib.Path(p).relative_to('theme-deep-blue')) for p in pathlib.Path('theme-deep-blue').rglob('*') if p.is_file()];
z.close()"
```

| 示例 | 演示能力 | 用到的 op |
| --- | --- | --- |
| `theme-deep-blue` | **修改主题**：强调色换深蓝，主题切换后仍生效 | `insert_after`（锚点后插入） |
| `custom-hello-tool` | **自定义工具**：新模块 + TOOLS 登记 schema + 分发器接线，Agent 多一个 `hello_demo` 工具 | `create_file` + 2×`insert_after`（跨 2 文件） |
| `settings-page-demo` | **自定义设置页**：新页面模块 + 导航项 + 页面构建器 + 构建方法，一次改 4 处集成新页面 | `create_file` + 2×`insert_after` + `append`（跨 2 文件） |
| `tray-quick-note` | **应用扩展**：托盘动作 + 后台服务，模块随插件包分发、主窗口启动时动态加载 | 无源码变更，靠 `copilot.json` 声明 |
| `py-syntax-lint` | **Python 语法高亮与纠错**（源码级「插入」）：新模块给聊天代码块着色，并新增 `check_python_syntax` 工具定位语法错误（只解析不执行）。原有代码行**一行未改**——只在模块尾部追加了一个包装函数重绑定 `_md_to_html` | `create_file` + 3×`insert_after`（跨 3 文件） |
| `prompt-optimizer` | **提示词优化**（源码级「替换」）：新增 `optimize_prompt` 工具把粗略提示词改写成六要素结构化提示词；并**替换** `agent_skills._base_prompt_parts` 的末行 `return parts`，把「提示词优化纪律」追加进系统提示词尾部 | `create_file` + `replace_module` + 2×`insert_after`（跨 3 文件） |
| `agent-scorecard` | **Agent 能力评分**（**纯运行时**，无源码变更）：包装 `execute_tool` / `AgentEngine.run` 采集耗时、工具/MCP/技能/子 Agent 调用与 token，任务结束弹评分窗，评分回传 Agent 并提示自我反省；运行时注入 `score_last_task` 工具 | 无源码变更，靠 `copilot.json` 服务 + 运行时挂钩 |

> 源码级变更（`source_ops.json`）一律写入**转译层**（用户数据目录下的源码覆盖层），
> 不直接改写应用源码文件：打包版安装目录只读、源码版也不污染工作区。
> **启用插件时自动写入、停用时自动回滚，并热加载受影响的模块**（无需手动应用、
> 也不用重启）；只有外观/主窗口等持有进程级状态的模块会在下次启动时生效。

> 「插入」与「替换」是源码级定制的两条路：
> **插入**（`append` / `insert_after`）不改动任何已有代码行，风险最低，适合加能力、包装函数、接线；
> **替换**（`replace_module` / `replace_file`）直接改写已有实现，用于改造既有行为，需自己保证替换后的代码完整可编译。
>
> `py-syntax-lint` 与 `prompt-optimizer` 的 `source/` 目录是**变更内容的可读副本**（便于学习与 review）；
> 插件实际执行的是 `source_ops.json` 里的 content，两者内容一致。

## 五类源码 op（source_ops.json）

```json
{"op": "create_file",     "path": "zhuzhu_Copilot/core/x.py", "content": "完整文件内容"}
{"op": "append",          "path": "zhuzhu_Copilot/core/x.py", "content": "…", "after": "锚点行(可选, 缺省追加到文件末尾)"}
{"op": "insert_after",    "path": "zhuzhu_Copilot/core/x.py", "anchor": "必须唯一的锚点行", "content": "…"}
{"op": "insert_after",    "path": "zhuzhu_Copilot/core/x.py", "line": 120, "content": "…"}
{"op": "insert_at",       "path": "zhuzhu_Copilot/core/x.py", "line": 120, "content": "在第 120 行之前插入"}
{"op": "replace_lines",   "path": "zhuzhu_Copilot/core/x.py", "start_line": 100, "end_line": 120, "content": "替换第 100~120 行"}
{"op": "replace_module",  "path": "zhuzhu_Copilot/core/x.py", "start": "起始锚点", "end": "结束锚点(可选)", "content": "…"}
{"op": "replace_module",  "path": "zhuzhu_Copilot/core/x.py", "start_line": 100, "end_line": 120, "content": "…"}
{"op": "replace_file",    "path": "zhuzhu_Copilot/core/x.py", "content": "完整文件内容"}
```

**两种定位方式**（同一 op 二选一）：
- **行号**（`line` / `start_line`..`end_line`，1 起含边界）：插件作者拿得到完整源码（如 GitHub 仓库）时最直接；
  `insert_at line=N` 表示「插到第 N 行之前」，`line=行数+1` 等价追加到文件末尾；
- **文本锚点**（`anchor` / `start`，须真实存在且唯一）：对版本差异更鲁棒。
- op 按**顺序**生效：前一条插入/替换会改变后续 op 看到的行号，多 op 改同一文件时请从后往前写行号。

- `path` 一律相对源码根（`src/`），**禁止绝对路径与 `..` 穿越**；
- `anchor`/`start` 必须取自目标文件里**真实存在且唯一**的字符串（先 `read_file` 看真实内容再设计）；
- 一个插件可带多条 op、跨多个文件——「插入任何源码文件 / 新建文件 / 多文件综合修改」就此实现；
- 受保护核心程序（`plugin_guard.py`、`security_engine/`、`_copilot_overlay_hook.py`、`main.py` 等）不可被改动。

## 应用与回滚

应用时会按原文件的换行风格写回（CRLF 文件不会被整篇改成 LF），改完立即逐文件 `py_compile`。

应用链路（`应用源码变更` 按钮 / `apply_copilot_plugin` 工具）：
**插件校验系统**（静态危险规则 + LLM 源码评审，危险即强制删除并提示）→ 逐文件备份 →
逐条应用 → 全部 `py_compile`（任一失败**整体回滚**，源码一行不动）→ 记录 `applied.json`。
回滚（`revert_copilot_plugin`）按备份还原：新建的文件删除、修改的文件还原；
应用后被手动改过的文件跳过不覆盖。

## 打包版也能源码级定制

安装目录不含源码：变更写入 `~/.zhuzhu_Copilot/source_overlay/`（镜像源码相对路径），
由 `src/_copilot_overlay_hook.py` 的导入钩子让 `zhuzhu_Copilot.*` 的导入**优先命中覆盖层**。
因此改主题、自定义 Agent、自定义工具、自定义语言、增删替换任何模块，在安装版上同样生效
（应用/回滚后**重启应用**生效）。
