---
name: custom-ui-ux
description: 自定义 AI 面板 UI/UX（Cordis 热插拔）：AI 完整自定义 PyQt6 界面，支持深色/浅色双主题与自定义主题色板，创建/编辑/切换/删除 UI/UX 包
---

# custom-ui-ux：自定义 AI 面板界面（热插拔 UI/UX）

当用户要求"自定义界面 / 换个风格 / 改 UI / 换个主题皮肤 / 重新设计面板"时使用本技能。

## 1. 理解 UI/UX 包
每个 UI/UX 包是一个目录：~/.zhuzhu_Copilot/ui_ux/<name>/，包含：
- ui_ux.json        元数据（name/description/is_builtin/theme 自定义主题色板）
- build_ui.py       必须：def build_ui(self): 构建面板
- build_welcome.py  可选：def build_welcome(self): 返回欢迎页 QWidget
- panel.qss         可选：QSS 样式表，深度定制全部组件外观（按钮/滑块/输入框/对话框等）
- resources/        可选：图标/字体/图片等资源文件
内置 default 包不可删除；自定义包加载失败会自动回退默认，无需担心破坏面板。
包可导出为 zip 资源包（manage_uiux op=export）分享给其他开发者，对方 op=import 即装即用。

## 2. 生成流程
1. 先问清用户想要的风格/布局/配色（信息不足用 ask_user，禁止瞎猜）。
2. 用 manage_uiux 工具列出已有包：参数 op=list。
3. 选择生成方式：
   - **整体生成（推荐）**：`create_uiux(description="风格/布局/配色描述", activate=false)`，
     内部走真实 LLM 生成 build_ui + 深/浅双主题色板 + panel.qss 并落盘，生成期间自动上报进度；
     activate=true 时生成后自动热插拔切换。适合「换个风格 / 重新设计面板 / 自定义界面」。
   - **手工精修**：自己写**完整自定义** build_ui.py 后 `manage_uiux op=create` 创建：
     {"op":"create","name":"包名(英文)","description":"一句话风格","build_ui":"完整代码",
      "build_welcome":"可选","theme":"{\"dark\":{...},\"light\":{...}} 可选自定义主题色板",
      "qss":"可选 QSS 样式表深度定制组件外观"}
     手工分多轮执行/自检耗时较长时，按阶段调用 `set_generation_progress(percent, message)`
     上报进度，让用户看到进展而非干等。
4. 可选 op=activate 立即切换（热插拔生效）；若未激活，让用户在 设置→UI/UX 自定义 里切换预览。

## 3. 契约（务必遵守，否则面板出错自动回退默认）
- def build_ui(self): 的 self 是 AgentPanel 实例。
- **默认必须完整自定义**：自建全部契约控件属性与侧边窗口，包括 self._root_lay /
  self.title / self.session_combo / self.new_btn / self.settings_btn / self.token_label /
  self.wf_label / self.msg_area / self.msg_lay / self._welcome_page / self.msg_stack /
  self.cmd_list / self.input / self.attach_btn / self.optimize_btn / self.model_combo /
  self.action_btn / self.todos_win / self.git_win / self.wt_win / self.code_win；
  且正确连接 self._new_session / self._open_settings / self._on_session_selected /
  self._send / self._on_action_clicked / self._pick_attachments / self._on_optimize_clicked /
  self._on_model_combo / self._on_cmd_selected / self._clear_chat 等已有方法。
  可选复用：self.toggle_token_stats() / self.open_token_stats() 弹出 token/上下文统计浮层
  （上游真实用量 + 占用进度条 + 阈值刻度 + 缓存命中率），无需自建统计界面。
- 仅当用户明确说"在默认基础上微调"时才允许 build_ui 先调用 self.build_default_ui() 再改样式。
- **PyQt6 环境（禁止 import 与 PyQt5 API）**：本项目基于 PyQt6，build_ui 的命名空间已注入全部
  所需组件/常量/函数，**禁止 import/from**（导入会加载失败回退默认）。禁止 PyQt5 专属写法：
  QFont.Bold → QFont.Weight.Bold、Qt.AlignLeft → Qt.AlignmentFlag.AlignLeft、
  Qt.Checked → Qt.CheckState.Checked、QFontMetrics.width → horizontalAdvance。
- **必须更改全部 UI/UX 组件（硬性要求，禁止任何组件保留默认外观）**：按钮（形状/圆角/渐变/
  内边距/悬停/禁用）、输入框（边框/圆角/聚焦）、下拉框（箭头/列表项高亮）、滚动条（轨道/滑块）、
  菜单 / Tooltip / 对话框、图标尺寸与颜色、字体大小粗细、间距留白、边框阴影、列表选中态，
  都要深度自定义样式。
- **必须提供 panel.qss（qss）**：用 QSS 统一覆盖按钮/输入框/下拉框/滚动条/菜单/Tooltip/
  对话框/列表等全部组件类型；颜色可用主题变量名（BG/TEXT/ACCENT 等）。
- **必须提供 theme 的 dark/light 两套色板**：ui_ux.json 的 theme 字段为 {"dark":{常量名:色值,...},
  "light":{常量名:色值,...}}，键必须是上面的主题常量名。提供后用户可在当前 UI/UX 下独立换肤。
- **4 个子面板完全自定义（布局/背景/位置）**：主面板之外有 4 个独立悬浮子窗口——任务清单
  self.todos_win、Git 面板 self.git_win、工作树 self.wt_win、代码预览 self.code_win。可用注入的类
  （TodosWindow/GitLogWindow/WorktreeWindow/CodePreviewWindow）或自定义 QWidget 子类替换，
  布局/背景（setStyleSheet/QSS）/控件全部自定义。位置默认由守卫自动停靠（todos/git/wt 堆叠主面板
  左侧、code 贴右侧）；对窗口置 `win._ai_managed = True` 后完全接管：守卫不再重定位/改大小/按设置
  开关隐藏，由你自行 move()/resize()/show()/hide() 自由摆放。数据接口（面板会自动调用）：
  todos_win.update_todos(list)；git_win.refresh() 与 git_win.list（QListWidget）；
  wt_win.refresh()（双击文件可触发 self._open_code_preview(path)，需窗口内保存主面板引用）；
  code_win.show_file(path)。4 个子面板同样必须深度自定义，颜色用主题常量，深/浅色两套适配。
- **按钮注册接口（与 UI/UX 解耦，恒可用）**：self.register_btn(btn_id, text=..., callback=...,
  tooltip=..., order=0) / self.unregister_btn(btn_id) / self.list_registered_btns()。
  注册的按钮恒渲染在面板顶部按钮栏——不管当前/以后切换任何 UI/UX 包、甚至完全重构界面，
  按钮都保持可见且点击回调可用。代码里可直接绑定任意回调；AI 对话运行中可用工具
  register_panel_btn op=register 注册（action 限内置功能：send/new_session/settings/
  attach/optimize/clear_chat），op=unregister 注销。
- **深度自定义组件**：build_ui 命名空间覆盖 Qt 全组件——布局（QVBoxLayout/QHBoxLayout/
  QGridLayout/QFormLayout）、控件（QLabel/QPushButton/QToolButton/QComboBox/QScrollArea/
  QStackedWidget/QListWidget/QWidget/QSlider/QCheckBox/QRadioButton/QLineEdit/QFrame/QMenu/
  QTabWidget/QSplitter/QProgressBar/QDialog/QScrollBar/QMessageBox/QButtonGroup）、绘图
  （QPixmap/QFont/QIcon/QColor/QPalette/QPainter/QPen/QBrush/QLinearGradient/
  QRadialGradient/QTextOption）、动画线程（QTimer/QPropertyAnimation/QVariantAnimation/
  QEasingCurve/QThread）、阴影特效（QGraphicsDropShadowEffect）等，可任意组合自定义组件。
- **panel.qss 深度样式**：包根目录 panel.qss（QSS）可定制全部组件外观——按钮形状/圆角/渐变、
  字体样式、图标大小、滑动条轨道/滑块、输入框、对话框、菜单、滚动条、Tooltip 等；
  用 manage_uiux op=create 的 qss 参数或 op=set_qss 写入。
- build_welcome(self) 返回一个 QWidget（无对话时显示）。
- 可用命名空间：上面全部 Qt 组件 + 主题常量 + 样式常量（_BTN_GHOST/_BTN_COMPACT/
  _BTN_GHOST_ACCENT/_BTN_PRIMARY/_BTN_DIM/_QCOMBO/_BTN_ICON/_BTN_DANGER）+ 工具函数
  （_line_icon/_svg_icon/_scrollbar_css/_app_icon_path/_std_icon/_file_icon）。
- **预览能力（内置，用户/你均可用来搭 Markdown/HTML/Web 渲染预览面板）**：
  - `pv = create_preview_widget(kind='markdown'|'html'|'text'|'web')` 或 `pv = PreviewPanel(kind=..., parent=self)`
    一键创建预览控件，加入任意布局即可用：
    pv.render_markdown(text)（Markdown→当前主题配色）、pv.set_html(html)、pv.set_text(text)、
    pv.set_url(url)。kind='web' 需已安装 QtWebEngine（未装自动回退静态渲染）。
  - `render_markdown_html(md, fragment=False, css_extra='')` 取完整 HTML 文档串（可嵌入他人文档）；
    `web_engine_available()` / `new_web_view()` 判断/创建浏览器内核。
- 禁止：import/from、os/subprocess/eval/exec/open/__import__、访问 self 之外的全局状态。
  只能操作 self 与以上命名空间。

## 4. 完整自定义示例（默认必须此风格，颜色用主题常量）
```python
def build_ui(self):
    root = QVBoxLayout(self)
    root.setContentsMargins(16, 14, 16, 14)
    self._root_lay = root
    # 侧边窗口必须创建
    self.todos_win = TodosWindow(self)
    self.git_win = GitLogWindow(self)
    self.wt_win = WorktreeWindow(self)
    self.wt_win.file_open_requested.connect(self._open_code_preview)
    self.code_win = CodePreviewWindow(self)
    # 顶栏（主题常量配色，深色浅色自适应）
    top = QHBoxLayout()
    self.title = QLabel("CUSTOM")
    self.title.setStyleSheet(f"color: {ACCENT}; font-size: 18px; font-weight: 800;")
    top.addWidget(self.title)
    self.session_combo = QComboBox()
    self.session_combo.setMinimumWidth(150)
    self.session_combo.setItemDelegate(_SessionStatusDelegate(self))
    self.session_combo.currentIndexChanged.connect(self._on_session_selected)
    top.addWidget(self.session_combo)
    self.new_btn = QPushButton("+")
    self.new_btn.clicked.connect(self._new_session)
    self.new_btn.setStyleSheet(_BTN_ICON)
    top.addWidget(self.new_btn)
    self.settings_btn = QPushButton("设置")
    self.settings_btn.clicked.connect(self._open_settings)
    self.settings_btn.setStyleSheet(_BTN_GHOST)
    top.addWidget(self.settings_btn)
    top.addStretch(1)
    self.token_label = QLabel("0 tk")
    self.token_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
    top.addWidget(self.token_label)
    self.wf_label = QLabel("")
    self.wf_label.setStyleSheet(f"color: {TEXT_DIM}; font-size: 12px;")
    top.addWidget(self.wf_label)
    root.addLayout(top)
    # 聊天区
    self.msg_area = QScrollArea()
    self.msg_area.setWidgetResizable(True)
    self.msg_area.setStyleSheet(
        "QScrollArea { background: transparent; border: none; }" + _scrollbar_css(8, 4, both=True))
    container = QWidget()
    container.setStyleSheet("background: transparent;")
    self.msg_lay = QVBoxLayout(container)
    self.msg_lay.addStretch(1)
    self.msg_area.setWidget(container)
    self._welcome_page = self._build_welcome()
    self.msg_stack = QStackedWidget()
    self.msg_stack.addWidget(self.msg_area)
    self.msg_stack.addWidget(self._welcome_page)
    root.addWidget(self.msg_stack, 1)
    self.cmd_list = QListWidget()
    self.cmd_list.hide()
    root.addWidget(self.cmd_list)
    # 输入行
    row = QHBoxLayout()
    self.input = _DropTextEdit()
    self.input.setPlaceholderText("给我描述你的任务吧…")
    self.input.submit.connect(self._send)
    self.input.setStyleSheet(
        f"QPlainTextEdit {{ background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER};"
        "border-radius: 10px; padding: 5px 10px; font-size: 14px; }}"
        f"QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}")
    row.addWidget(self.input, 1)
    self.attach_btn = QPushButton("+")
    self.attach_btn.clicked.connect(self._pick_attachments)
    self.attach_btn.setStyleSheet(_BTN_ICON)
    row.addWidget(self.attach_btn)
    self.optimize_btn = QPushButton("优化")
    self.optimize_btn.clicked.connect(self._on_optimize_clicked)
    self.optimize_btn.setStyleSheet(_BTN_GHOST)
    row.addWidget(self.optimize_btn)
    self.model_combo = _ArrowComboBox()
    self.model_combo.setMinimumWidth(150)
    self.model_combo.setStyleSheet(_QCOMBO)
    self.model_combo.currentIndexChanged.connect(self._on_model_combo)
    row.addWidget(self.model_combo)
    self.action_btn = QPushButton("发送")
    self.action_btn.setStyleSheet(_BTN_PRIMARY)
    self.action_btn.clicked.connect(self._on_action_clicked)
    row.addWidget(self.action_btn)
    root.addLayout(row)
    return self
```
子面板完全自定义示例（放在 build_ui 内；_ai_managed=True 自由摆放，默认则自动停靠）：
```python
# 方案 A：继承内置类深度改版（数据接口自动保留）
class _MyGit(GitLogWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("myGitWin")
        self.setStyleSheet(f"QWidget#myGitWin {{ background: {CARD}; "
                           f"border: 2px solid {ACCENT}; border-radius: 10px; }}")
        self.setFixedWidth(320)          # 自定义宽度
        self._ai_managed = True          # 完全接管：守卫不再重定位/改大小
        self.move(30, 80)                # 自由摆放位置
        self.show()
self.git_win = _MyGit(self)
# 方案 B：全新自定义窗口（实现所需接口即可）
class _MyTodos(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setObjectName("myTodos")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget#myTodos {{ background: {CARD}; }}")
        self.setFixedWidth(240)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        self._list = QListWidget()
        self._list.setStyleSheet("QListWidget { border: none; }")
        lay.addWidget(self._list)
    def update_todos(self, todos: list):
        self._list.clear()
        for t in todos:
            self._list.addItem(str(t))
self.todos_win = _MyTodos(self)
self.todos_win._ai_managed = True        # 自由摆放，守卫不干预
self.todos_win.move(60, 150)
self.todos_win.show()
```
按钮注册接口示例（放在 build_ui 内；与 UI/UX 解耦，任何界面都渲染在顶部按钮栏）：
```python
self.register_btn("quick_send", text="快速发送", callback=self._on_action_clicked,
                  tooltip="发送/停止当前任务")
self.register_btn("clear", text="清空", callback=self._clear_chat, order=1)
```
主题色板示例（随 create 的 theme 参数输出，两套都要）：
```json
{"dark": {"BG": "#0B0F1A", "PANEL": "#12182B", "CARD": "#1A2138", "TEXT": "#E8EAF6",
          "TEXT_DIM": "#9AA3C0", "ACCENT": "#6D5DF6", "ACCENT_HOVER": "#8B7CF8",
          "HOVER": "#232C48", "USER_BG": "#6D5DF6"},
 "light": {"BG": "#F6F5FF", "PANEL": "#FFFFFF", "CARD": "#FFFFFF", "TEXT": "#1E1B36",
           "TEXT_DIM": "#6B6790", "ACCENT": "#6D5DF6", "ACCENT_HOVER": "#5A4BE0",
           "HOVER": "#EDEBFC", "USER_BG": "#6D5DF6"}}
```
panel.qss 样式表示例（随 create 的 qss 参数或 op=set_qss 写入，深度定制组件外观）：
```css
QPushButton { border-radius: 16px; padding: 8px 20px; }
QPushButton#sendBtn { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
    stop:0 #6D5DF6, stop:1 #9A7BFF); border-radius: 20px; font-weight: 800; }
QSlider::groove:horizontal { height: 6px; border-radius: 3px; background: #2A3350; }
QSlider::handle:horizontal { width: 18px; height: 18px; margin: -6px 0;
    border-radius: 9px; background: #6D5DF6; }
QLineEdit { border-radius: 12px; padding: 8px 14px; }
QScrollBar::handle:vertical { border-radius: 4px; }
```

## 5. 编辑、样式、主题与共享
- 读取包内容：manage_uiux op=read，传 name + part（build_ui/build_welcome/theme/qss/meta）。
- 修改 build_ui：manage_uiux op=update（name + 新代码），或 read_file/edit_file 直接改。
- 深度定制组件样式：manage_uiux op=set_qss（name + qss QSS 样式表），定制按钮形状/滑动条/
  输入框/对话框/图标/字体等全部组件外观。
- 修改主题色板：manage_uiux op=update_theme（name + theme JSON），一键换肤。
- 依赖打包（插件/MCP server）：包可在元数据声明依赖（op=create 传 plugins=["插件名"]、
  mcp=["MCP名"]，或 op=deps name+plugins+mcp 更新）。导出 zip 时对应插件目录与 MCP 配置
  会一并打包，对方导入自动安装依赖，实现"即装即用"。
- 复制包：manage_uiux op=duplicate（name + new_name），便于基于现有包派生新风格。
- 导出 zip 共享：manage_uiux op=export（name + dest 目标目录），生成 zip 资源包分享给开发者
  （若声明了依赖会含 plugins/ 与 mcp_servers.json）；对方导入：manage_uiux op=import
  （zip_path），自动校验安全（防路径穿越/文件白名单）并安装依赖，同名覆盖。
- 切换：manage_uiux op=activate，name 传包名（"default" 切回默认）；面板热插拔立即重建。
- 删除用户包：manage_uiux op=delete（内置包会被拒绝）。
- 列出：manage_uiux op=list；查看当前：op=get。

## 6. 收尾
- 汇报：创建的包名、是否带自定义主题、保存位置、如何切换/删除。
- 强调安全：出问题会自动回退默认界面，不会破坏面板功能。
