# zhuzhu Copilot 性能基线报告

日期：2026-10-02 · 环境：Windows / Python 3.13.13 / PyQt6 / `QT_QPA_PLATFORM=offscreen`

探针：`scripts/perf_bench.py`（各阶段独立子进程，取 min/median/p95）
测量环境已剔除宿主注入的 `PYTHONPATH=...WorkBuddy.../sitecustomize.py`（实测每次解释器启动额外 ~790ms，打包 exe 不存在该路径，不剔除会把基线整体抬高 0.8–1.2s）。

---

## 一、结论先行

1. **启动（到面板可见）约 5.8s**，目标 2.2s，需压缩 2.6×。
2. **仅把启动动画画到屏幕上就要 ~2.9s**（端到端实测 2.69–3.58s），其中 `import agent_panel` 一项占 2.27s。
3. 三项交互超标：**主题切换 2129ms**（目标 300ms，超 7×）、设置对话框 672ms、会话切换 562ms。
4. **阻塞项：当前工作区应用启动不了** —— 半途回退删除了 39 个顶层定义，构造 `AgentPanel` 必抛 `NameError`。

---

## 二、启动链路实测（内部计时，median / p95）

| 阶段 | median | p95 | 说明 |
|---|---:|---:|---|
| 解释器空启动（wall） | 513 ms | 590 ms | 固定成本 |
| `import PyQt6`（含 QtWebEngineWidgets） | 583 ms | 737 ms | 其中 QtWebEngineWidgets 累计 ~210ms |
| **`import agent_panel`（含依赖）** | **2273 ms** | 2498 ms | 最大单项 |
| ├ 依赖导入 | ~1515 ms | | helpers/agent_tools/agent_llm/agent_tts… |
| ├ 模块体执行（pyc 命中） | 524 ms | | |
| └ 源码编译（**pyc 未命中时另加**） | +3779 ms | | 见「结构性发现 1」 |
| `QApplication` 创建 | 91 ms | 94 ms | |
| **配置解密（PBKDF2 ×2）** | **1143 ms** | 1480 ms | 200k 迭代，enc/mac 各派生一次 |
| **`AgentPanel` 构造** | **1313 ms** | 1485 ms | |
| `show()` + 首帧 | 398 ms | 583 ms | |
| `singleShot(0)` 延迟初始化 | 1482 ms | 2198 ms | `_init_sessions` 占 381ms |
| `prewarm_copilot_panel` | **>120 s** | | 触发**全盘应用扫描**，探针超时 |

端到端（进程启动 → 启动动画首帧）：**2.69 s / 2.85 s / 3.58 s**（3 次）。
注意：`main.py` 当前固定 `QTimer.singleShot(2000, ...)` 后再开面板，这一项纯等待就吃掉 2.0s 预算中的绝大部分。

### 启动时序推算（现状）

```
0.00  进程启动
0.49  解释器就绪
1.07  PyQt6 导入完成
1.16  QApplication 就绪 → 启动后台预加载线程（import agent_panel 2.27s + 解密 1.14s）
1.20  启动动画显示
4.56  预加载线程结束（GIL 下与主线程基本串行）
5.87  AgentPanel 构造 1.31s + show/首帧 0.40s → 面板可见
7.35  singleShot(0) 初始化 1.48s 完成
```

---

## 三、交互实测（同一面板实例，取多次最优）

| 操作 | 实测 | 目标 | 判定 |
|---|---:|---:|---|
| resize 气泡重建 | 68 ms | 200–300 ms | 达标 |
| 会话切换 `_switch_to` | 562 ms | 200–300 ms | 超 1.9× |
| 设置对话框构造 | 672 ms | 200–300 ms | 超 2.2× |
| 主题切换 `_retheme` | **2129 ms** | 200–300 ms | **超 7×** |

（`ChatTurn.render` 因探针调用签名不匹配未取到值，下轮修正后补测。）

---

## 四、结构性发现

### 1. f-string 导致 CPython PEG 解析退化（超线性）

`agent_panel.py`（20 696 行 / 857 KB / 1075 处 f-string）：

| 场景 | 耗时 |
|---|---:|
| `ast.parse` 原样 | 3847 ms |
| 仅去掉 `f` 前缀（长度不变） | **415 ms** |
| 合成基线：2 万行普通语句 | 214 ms |

合成复现（每行一个含嵌套引号的 f-string）确认**超线性**：

| 行数 | f-string | 普通字符串 | 比值 |
|---:|---:|---:|---:|
| 250 | 20.4 ms | 2.3 ms | 8.9× |
| 1000 | 208.2 ms | 9.9 ms | 21.0× |
| 4000 | **2894.7 ms** | 40.2 ms | **72.0×** |

影响：`.pyc` 命中时免付（实测命中 2.1s vs 未命中 5.7s）；但 **pyc 失效/首次运行就要额外付 ~3.8s**。构建期必须保证 pyc 常驻。

### 2. PBKDF2 派生了两次

`agent_llm._pbkdf2_derive` 200k 迭代，加密/认证各派生一次 → 1.14s。

### 3. 预热在主线程触发全盘应用扫描

`prewarm_copilot_panel` 在面板首帧后 600ms 于主线程执行，探针 120s 未结束（全盘扫描）。这是「打开后卡住」的直接来源。

### 4. 工作区处于半途回退状态（阻塞）

`core/app_glass.py` 等 5 个文件被删，`agent_panel.py` 相对 HEAD 少了 **39 个顶层定义**（含 `app_glass` 导入、`_glass_on`、`_harden_combo_popup`、`_popup_surface`…）。运行时必抛 `NameError`，应用无法启动。
本报告中的面板/交互数据是在探针里用「万能 stub」补齐这 39 个符号后测得的，产品代码未做任何改动。

---

## 五、优化方案（按预期收益排序）

| # | 措施 | 预期收益 | 风险 |
|---|---|---:|---|
| A | 启动动画 2000ms → 600–700ms（最短展示 + 就绪即淡出） | −1.3 s | 观感，需确认 |
| B | PBKDF2 两次派生合并为一次（HKDF-Expand 出 enc/mac） | −0.57 s | 无（安全性等价） |
| C | `agent_panel` 依赖延迟导入（helpers/agent_tools/agent_tts/agent_screen 等移入函数内） | −1.1 s | 需保证无循环依赖 |
| D | 面板构造拆分：扩展面板 / 侧栏滚动条 / 文件观察者延后 | −0.9 s | 低 |
| E | 构建期 `compileall` 保证 pyc 常驻 | 避免 +3.8 s | 无 |
| F | 预热的全盘扫描移出主线程并分片 | 消除主线程 >120s 阻塞 | 中（需保证幂等） |
| G | `_retheme` 改为就地重绑色板，不重建控件树 | 2129 → <300 ms | 中（需覆盖全部控件） |
| H | 设置对话框 13 页改为按需构建（现仅首页懒建，保存时才补齐） | 672 → <300 ms | 低 |
| I | 会话切换改为增量渲染 | 562 → <300 ms | 中 |
| J | 延迟 `QtWebEngineWidgets` 导入 | −0.2 s | **需验证 Qt「先于 QApplication 导入」约束** |

预期合计：启动 5.8s → ~2.2s；三项超标交互降至 300ms 内。

---

## 六、前置阻塞（需先决策）

玻璃系统去留二选一，否则无法进入改造：

- **彻底移除**：删掉 `agent_panel` 残留引用，与工作区已有的 1253 行回退方向一致；玻璃（背景模糊/实时重绘）本身是性能大户，利于达标。
- **恢复**：`git checkout HEAD -- src/zhuzhu_Copilot/core/app_glass.py scripts/_probe_glass_restyle.py src/zhuzhu_Copilot/skills/app-background/SKILL.md tests/test_app_glass.py tests/test_glass_surface_qss.py`，回到 HEAD 的玻璃架构。
