# 修复：任务清单（TODO）按会话隔离（2026-09-26）

## 一、问题现象

上游返回 HTTP 400 上下文超限（示例：`deepseek-flash` 报 `maximum context length is 1048576
tokens, however you requested 1395754 tokens`，1076 条消息 / 1934510 字符）。

排查结论：任务清单原先落在**单一全局文件** `~/.winapp_migrator/agent/todos.json`，并且被引擎
作为对话末尾的独立 todo 消息**注入每一个会话的上下文**（`agent_engine._todo_text`）。于是

- 任一会话写下的清单会被**所有**会话读到并注入 → 多会话并发时互相覆盖进度、任务串台；
- 每个会话都白背一份与本任务无关的清单，长任务/多会话场景下持续推高请求上下文。

用户要求：**每个会话独享 todos，会话与会话之间不共享清单，只有当前会话的 Agent 才能查看
与修改**（隔离方式对齐已有的「共同上下文空间」`agent_context`）。

## 二、隔离设计

沿用共同上下文空间的思路：**会话 id 即作用域**，只有该会话的 Agent（主 Agent 与它派发的
子 Agent）能看到自己那一份。

| 维度 | 改动后 |
| --- | --- |
| 存储位置 | 每会话一份 `~/.winapp_migrator/agent/todos/<会话 slug>.json` |
| 作用域解析 | 显式传入会话 > 当前会话（线程局部 → UI 当前会话）> 空 |
| 无会话作用域 | 无 UI 进程 / 单元测试沿用兼容文件 `todos.json`（不注入任何会话） |
| 缓存 | 从「单文件一份」改为**按文件分键** + 加锁，多会话并发各读各写各的 |
| 文件名安全 | 非法字符归一化（`a/b` → `a_b`），发生替换时附 8 位哈希防不同会话撞同一文件 |

## 三、代码改动

1. `core/agent_tools.py`（存储层 + 工具层）
   - 新增 `TODO_DIR`、`todo_scope()`、`todo_file()`、`_todo_slug()`、`_read_todo_list()`、
     `save_todos()`、`clear_todos()`、`drop_todos()`；`load_todos(conv_id=None)` 支持指定会话。
   - `_update_todo` 写入本会话文件；全部完成时只清空本会话（`clear_todos()`）。
   - 缓存由全局单条改为 `{文件路径: {指纹, 清单}}`，`_TODO_MAX_CACHE` 上限淘汰，读写加锁。
2. `core/agent_engine.py`
   - `_todo_text()` 显式读 `agent_tools.load_todos(self.conversation)`——只注入本引擎所属会话。
   - 新增 `_exec_tool()`：在工具执行 worker 线程内重新落地本会话作用域后再执行内置工具。
     工具跑在并发线程池 / `_call_with_stop` 派生线程里，线程局部作用域不会继承，否则后台
     并发会话会读写到别的会话的数据（任务清单、共同上下文空间同受影响）。
3. `ui/agent_panel.py`
   - `_on_todos_clear()` 改走 `agent_tools.clear_todos()`：只清当前会话；
   - 删除会话时 `agent_tools.drop_todos(sid)`：该会话的清单随会话一并回收（与其他会话级
     数据一致，见 `_delete_session` 里共同上下文空间的回收）；
   - 切会话 / 新会话 / 工具回调刷新任务清单窗口时均按当前会话解析。

## 四、测试

新增 `tests/test_session_todos_isolation.py`（12 例）：

1. 清单文件按会话解析（同会话同文件、异会话异文件、无作用域回退兼容文件）
2. 工具层读写隔离：A 会话 `update_todo` → B 会话读不到，回到 A 内容完好
3. 引擎层：工具在 worker 线程内执行仍落在本引擎所属会话；`_todo_text` 只读本会话
4. 全部完成自动清空只清本会话
5. 删除会话（`drop_todos`）只回收本会话清单
6. 并发会话（4 线程 × 20 次写）各写各的、互不串写
7. 会话 id 非法字符归一化与唯一性
8. 源码守卫：UI 清空/刷新/删会话走会话级 API，引擎注入与 worker 作用域落地

`tests/test_longtask_perf.py::test_load_todos_reflects_writes` 同步改为显式「无会话作用域」
调用（清单按会话隔离后，缺省调用会解析到当前会话）。

```bash
python -m pytest tests/test_session_todos_isolation.py tests/test_longtask_perf.py -q
```

## 五、生效方式与兼容说明

- 已安装程序内置的是编译后的核心模块（`core/*.pyd` 打进安装包），改动需**重新构建并安装**
  后才在已装客户端生效；源码仓库内即时生效。
- 兼容：旧的全局 `todos.json` 保留在磁盘上、且只服务「无会话作用域」场景（无 UI 进程/单测），
  不会被任何会话读取或注入——升级后各会话从空清单开始，符合「会话独享」的预期。
