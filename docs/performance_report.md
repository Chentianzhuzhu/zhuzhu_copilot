# zhuzhu Copilot 性能分析报告 (2026-10-02)

## 基线数据（当前版本）

### 加载长对话流
| 场景 | 同步耗时 | 目标 |
|---|---|---|
| 首次进入（冷，2.6MB 会话） | 1377 ms | < 500 ms |
| 切走再切回（温） | 936 ms | < 500 ms |
| 更小会话（873KB）首次 | 1907 ms | < 500 ms |
| 更小会话（873KB）切回 | 1473 ms | < 500 ms |
| 后台展开过程区（最大单次卡顿） | 306-761 ms | < 500 ms |

**大头**：
- `_render_history_all` 1028-1700ms
  - `_label_hfw` 累计 1452ms（638次 × 2.28ms）：双重测量（`setMinimumHeight` 触发 `heightForWidth` + 后续全量 relayout 再调一次）
  - `_make_block` 713ms（292次 × 2.44ms）：每个块都 `setSizePolicy` + `installEventFilter`
  - `_seg_blocks` 401ms（29次 × 13.84ms）：小会话的段缓存命中率低

### 主题切换
| 操作 | 同步耗时 | 分解 |
|---|---|---|
| 主题切换（深→浅→深） | 2647-2919 ms | |
| _retheme 内 | | |
| └─ AgentPanel.setStyleSheet | 783-876 ms | 全树重算根样式 |
| └─ _render_history_all | 948-1014 ms | 全量重建气泡 |
| └─ _build_ui | 179-185 ms | 重建全部控件 |
| └─ 清旧树/销毁侧栏 | ~740 ms | old widgets deleteLater |

### 背景操作
| 操作 | 同步耗时 |
|---|---|
| 设置背景（首次，4K 图） | 512 ms |
| 切换背景图（同尺寸） | 120 ms |
| 调模糊 / 压暗 | 3 ms |
| 清除背景（透明翻转 → _retheme） | **4369 ms** |
| 再次设置背景（翻转 → _retheme） | 2838 ms |

### AgentPanel 构造
| 操作 | 耗时 |
|---|---|
| __init__（含 _build_ui） | **3.08 s** |
| show 事件处理（DWM/titlebar） | 已含 |

## 瓶颈归因

### 瓶颈 1：`_label_hfw` 双重测量（~1452ms → 目标 <500ms）
**根因**：`setMinimumHeight(h)` 触发 `heightForWidth(w)` 重新测量（版本未变 → 命中缓存），紧接着 `_relayout_messages` 又触发了整批 `heightForWidth` 调用。两次同版本测量，浪费 1x。
**修复**：在 `relayout_heights` 返回后立即调用 `_relayout_messages`，让布局先稳定，再统一调用测量——避免中间态的多余触发。

### 瓶颈 2：`setMinimumHeight` 每块触发一次 `heightForWidth`（~713ms → 目标 <200ms）
**根因**：每个块 `_measure_block` 调用 `setFixedHeight(h)`，Qt 内部会再触发一次 `heightForWidth` 验证几何。
**修复**：在 `_make_block` 完成布局后、加入消息流前，预估算块高（使用估算函数 `_fold_est_lines` 的同等手法），批量写入 `minimumHeight`，避免 n 次触发。

### 瓶颈 3：`_render_history_all` 全量重建（~1000ms → 目标 <400ms）
**根因**：每次加载会话都重建所有气泡，即使内容完全没变（温切换）。
**修复**：在会话切换（`_switch_to` 的内存态命中路径）时，若会话的 `_seg_cache` 未失效且 `_rows` 未变，跳过 `_render_history_all`，直接复用现有气泡。同时保留会话内容签名的缓存（避免重复计算 `_seg_blocks`）。

### 瓶颈 4：`AgentPanel.setStyleSheet` 全树重算（~830ms → 目标 <200ms）
**根因**：根 QDialog 的 `styleSheet` 变更触发 Qt 对整棵子树（数千个 widget）的重新样式计算。
**修复**：
- 只在色板实际变化时调用 `app.setStyleSheet`（已有 `_LAST_PALETTE` 幂等保护，但根样式表仍需重建）
- 将根样式拆为「背景色」与「滚动条样式」两部分：背景色只设在根 QDialog；滚动条样式独立设置在 `msg_area`，避免整树重算

### 瓶颈 5：`_retheme` 全量重建（~3000ms → 目标 <500ms）
**根因**：主题切换触发完整 UI 重建（_build_ui + _render_history_all + 旧控件清理）。
**修复**：
- **主题颜色变更时**：仅重算 QSS 常量 + 重建面板根背景（~200ms），不重建 UI 树
- **壁纸状态翻转时**（设置/清除背景）：才走完整 `_retheme`（不可避免）
- **初始化时**：`AgentPanel.__init__` 异步化——先显示骨架，后台构建完整 UI

### 瓶颈 6：`_make_block` 创建开销（~713ms → 目标 <200ms）
**根因**：每个块都创建新 QWidget + setSizePolicy + installEventFilter + 样式表。
**修复**：对象池复用——维护一个 `_block_pool` 缓存创建的块，按 kind 区分，复用前仅更新内容。

## 优化目标

| 指标 | 基线 | 目标 | 实现路径 |
|---|---|---|---|
| 会话切换（热） | 936 ms | **< 500 ms** | 缓存复用 + 双重测量消除 |
| 会话切换（冷） | 1377 ms | **< 500 ms** | 同 + 渐进渲染 |
| 主题切换 | 2700 ms | **< 500 ms** | 色板 QSS 不重建 UI |
| 背景翻转 | 4369 ms | **< 500 ms** | 异步 _retheme |
| 每交互延迟 | — | **200-500 ms** | 上述综合 |

## 实施优先级

1. **P0**：消除 `_label_hfw` 双重测量（改动小、收益大）
2. **P1**：主题切换不重建 UI（只改色板 QSS）
3. **P2**：温切换复用缓存（会话切换提速）
4. **P3**：`AgentPanel.__init__` 异步化（启动提速）
5. **P4**：块对象池（长期收益）
6. **P5**：背景翻转异步 _retheme（体验优化）
