# 多功能增强设计：子面板拖动 / 音乐播放器 / 代码 diff 高亮

日期：2026-08-28

## 1. 子面板自由拖动 + 位置持久化

### 现状
- 4 个子面板：TodosWindow / GitLogWindow / WorktreeWindow（左侧停靠列）+ CodePreviewWindow（右侧停靠）
- 全部继承 `_RoundedFloatWindow`（无边框、圆角蒙版）
- 位置由 AgentPanel 的 `_sync_*_win` 系列方法锚定在主面板侧边自动定位，`_place_owned` 每帧归位
- 已有 `_ai_managed` 机制：AI 接管的面板跳过强制归位，守卫仅抬升

### 设计
- 给 `_RoundedFloatWindow` 增加顶部可拖拽把手条带（约 24-28px），`eventFilter`/`mouseMoveEvent` 实现拖动
- 拖动结束（mouseRelease）后把窗口位置写入 QSettings：`panel_pos/<objectName>`（如 `glassTodos`/`todosWin`）
- 新增面板自定义位置标记：`w._user_moved = True`；`_sync_*_win` 及守卫对 `_user_moved` 面板跳过强制定位（与 `_ai_managed` 并列），仅 `raise_`
- 启动/显示面板时（`showEvent`）读取 QSettings 恢复保存位置；无记录走默认停靠
- 设置页提供「重置面板位置」按钮：清除全部 `panel_pos/*` 并复位 `_user_moved`，下次同步回归默认停靠

## 2. 音乐播放器（设置页新页签）

### 存储
- 上传的 MP3/WAV 复制到 `~/.zhuzhu_Copilot/music/`
- 歌单：扫描该目录（按名排序）；播放状态写入 `~/.zhuzhu_Copilot/music/player.json`：
  `{current_index, position_seconds{文件名:秒}, volume, mode, autoplay}`

### 播放内核
- 新建 `src/zhuzhu_Copilot/core/music_player.py`，基于 pygame.mixer（项目已依赖 pygame 且随包发布）
- 封装：load/play/pause/resume/stop/set_position/set_volume/next/prev、信号（位置更新、结束、出错）
- 模式：顺序 / 随机 / 单曲循环（enum）
- 异步：pygame 在低层线程，UI 通过 QTimer(500ms) 轮询 `get_pos()+base` 更新进度条

### UI（设置页新页签「音乐」）
- 上传按钮（QFileDialog 多选 .mp3/.wav，复制入库）
- 歌单 QListWidget（双击播放；右键删除）
- 控制行：上一首 / 播放暂停 / 下一首、播放模式切换按钮、音量滑块
- 进度条 QSlider（可拖动 seek）+ 时间标签（当前/总时长）
- 自动播放开关（checkbox；打开 AI 面板自动播放）
- 关闭提醒？不必要；保存行为：进度每 5s 节流写入 player.json，音量/模式即时写
- 设置页关闭不销毁播放（后台继续播放、状态由面板持有）
- 实现为 `_build_music_page`；音乐控件常驻（对话框关闭后由全局单例 `music_player` 继续播放）

### 自动播放
- AgentPanel showEvent 时，若 `player.json.autoplay=true` 且未在播放 → 从上次进度/歌单继续

## 3. AI 代码变更 diff 高亮（预览面板）

### 现状
- CodePreviewWindow.show_text 用 QPlainTextEdit + 语法高亮展示代码
- AI 写文件走 execute_tool（write_file/edit_file），当前无差异反馈

### 设计
- AgentPanel 在工具执行回调（_confirm 后/执行结果返回处）对 `write_file`/`edit_file` 捕获执行前内容与执行后内容：
  - 新增 `_on_file_changed(path, old, new)` → `code_win.show_diff(path, old, new)`
- CodePreviewWindow 增加 `show_diff(path, old_content, new_content)`：
  - 用 `difflib.unified_diff`（n=0 上下文）解析增删块
  - 构造合并视图写入 QPlainTextEdit：新增行绿色底 + 行首 `+`，删除行红色底 + 行首 `−`，未变行正常
  - setExtraSelections 或纯文本行号前缀两种方案——采用行前缀方案（简单、可复制、滚动可靠）
  - 自动滚动到首个变更行
  - `QTimer.singleShot(2000)` → 重新 `show_text(path)` 恢复正常显示
- 面板未打开该文件时：自动 `show_file(path)` 展示 diff

## 验收
1. 四个子面板均可拖动；重启后位置恢复；重置按钮有效
2. 设置页音乐：可上传 mp3/wav、播放/暂停/上下曲/进度拖动/音量/三种模式/删除；重启后歌单与进度、音量、模式保留；自动播放开关生效
3. AI 增删代码 → 预览面板红绿 +/− 高亮并滚动到变更行，2 秒后恢复正常