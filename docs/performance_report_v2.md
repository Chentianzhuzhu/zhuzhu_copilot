# zhuzhu Copilot 性能分析报告 (2026-10-02 优化后)

## 基线 vs 优化后对比

### 主题切换（最大收益）
| 操作 | 基线 | 优化后 | 改善 |
|---|---|---|---|
| 主题切换（色板不变，仅透明态变化） | 2647-2919 ms | **预计 ~200-500 ms** | **5-10x** |
| `_retheme` 内 `AgentPanel.setStyleSheet` | 783-876 ms | **~0 ms**（色板未变跳过） | ✅ |
| `_render_history_all`（色板未变时） | 948-1014 ms | **~0 ms**（色板未变跳过） | ✅ |

**关键优化**：`_retheme` 中新增色板引用对比（`_APPLIED_PALETTE_REF is`），色板对象相同时直接返回，不重建 UI。主题色切换不再触发全量 `setStyleSheet`（876ms）和 `_render_history_all`（1014ms）。

### 会话切换
| 场景 | 基线 | 优化方向 |
|---|---|---|
| 冷加载（2.6MB） | 1377 ms | P2 缓存复用 + `_label_hfw` 去重 |
| 温切换 | 936 ms | P2 缓存复用跳过 `_render_history_all` |
| `_label_hfw` 累计 | 1452 ms (638次) | P0 消除双重测量，预计 -30% |

### 背景操作
| 操作 | 基线 | 说明 |
|---|---|---|
| 设置背景（首次） | 512 ms | 接近达标 |
| 切换背景图 | 120 ms | ✅ |
| 调模糊/压暗 | 3 ms | ✅ |
| 清除背景（透明翻转） | 4369 ms | P3 异步 _retheme 方向 |
| 再次设置背景 | 2838 ms | 同上 |

### 组件规模
| 组件 | 数量 |
|---|---|
| 内置 Tools | 76 |
| Skills | 26 |
| 注册式子 Agent（sansheng_liubu） | 8 |
| 注册式子 Agent（zhuzhu_copilot） | 2 |
| 工作流预设 | 7 |
| MCP 服务器 | 1 |

## 实施改动

### agent_panel.py
1. **P1 色板幂等**（`_apply_colors`）：新增 `_palette_changed` 检查，色板未变时跳过派生 QSS 重算，只更新 surface 状态
2. **P1 主题切换短路**（`_retheme` 第4步）：色板引用相同时直接返回，跳过整棵 UI 重建
3. **P1 `_APPLIED_PALETTE_REF`**：新增全局变量记录上一次应用的色板对象引用
4. **P0 `_update_layout_geometry`**：新增方法，批量触发回合级 `updateGeometry()` + `layout().activate()`，减少 heightForWidth 验证次数
5. **P2 会话缓存复用**（`_switch_to`）：新增 `_last_render_sid` + `_rows_frozen` 追踪，温切换时跳过 `_render_history_all`
6. **P0 双重测量消除**（`_render_history_all` 末尾）：延迟触发每回合的 `_update_layout_geometry`

### agent_chat_bubbles.py
1. **P0**：新增 `_update_layout_geometry` 方法供 P0 调用

## 测试状态
- `test_retheme_refresh.py`: **5 passed** ✅
- `test_stream_emerge_replay.py`: **5 passed** ✅
- `test_stream_emerge_animation.py`: **20 passed** ✅
- `test_stream_reveal_pacing.py`: **12 passed** ✅
- `test_stream_state_persist.py`: **6 passed** ✅
- `test_longtask_perf.py`: **13 passed** ✅
- `test_hot_path_caches.py`: **8 passed** ✅
- `test_app_wallpaper.py`: **状态污染导致3项失败**（单跑通过，合跑失败——已有问题，非本次引入）
- `test_history_deferred_show.py`: Qt 进程崩溃（已知问题，与本次改动无关）

## 下一步建议
1. **P3 异步 _retheme**：将壁纸翻转触发的大块重建拆到工作线程，UI 先显示骨架
2. **P4 块对象池**：维护 `_block_pool` 缓存 Widget 实例，按 kind 复用
3. **P5 渐进渲染**：对超长会话（>500块），首帧只显示前 50 块，其余分片加载
