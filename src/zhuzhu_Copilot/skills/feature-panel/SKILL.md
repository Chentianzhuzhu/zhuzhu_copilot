---
name: feature-panel
description: 用自然语言创建/扩展 AI 主程序里的自定义功能面板（独立浮窗）。当用户要求"加一个XX面板/编辑器面板/PPT演示面板/SSH连接服务器面板/仪表盘面板"时使用：确定面板能力 → 照模板写 build_panel 代码 → register_feature_panel 注入当前工作流 → 自动扫描挂载。禁止 mock，控件必须真实可用。
---

# feature-panel：自然语言创建自定义功能面板

当用户要求"添加一个…面板"（如 编辑文件面板 / PPT 演示面板 / SSH 连接服务器面板 / 数据可视化面板 / 系统监控面板 等）时使用本技能。

## 触发词
面板、功能面板、扩展面板、自定义面板、编辑器面板、PPT演示面板、演示面板、SSH连接面板、连接服务器面板、监控面板、仪表盘面板、给AI加个面板。

## 一、先确认面板契约（扩展面板 API 现状）
- 扩展面板由 `zhuzhu_Copilot.core.agent_panels` 统一管理：支持 **code / workflow(panel.py) / plugin(panel.py)** 三种来源，同名覆盖，热更新。
- **锚点（检查结论）**：当前只支持「独立浮窗」（左侧列底部堆叠，可拖动/关闭）。**暂不支持**嵌到设置页或 AI 主面板内部。
- 面板文件约定（panel.py 顶部可选）：
  ```python
  TITLE = "面板标题"
  WIDTH = 320
  HEIGHT = 300
  def build_panel(owner) -> QWidget:   # owner 为 AgentPanel 实例；仅允许返回一个真实控件
      ...
  ```
- 面板运行在程序进程内，可 `from PyQt6.QtWidgets import ...` 使用 Qt；读写文件请用标准库，调用 AI 能力可 `from zhuzhu_Copilot.core import agent_tools`。
- 保存后由 UI 自动扫描挂载（rebuild_uiux）；`panel.py` 源码被修改后热更新自动重建。

## 二、生成流程（必走，禁止跳过）
1. **问清面板用途**：面板要解决什么问题、展示/操作什么数据（如编辑哪个目录的文件、演示哪个 pptx、连哪台服务器）。数据/凭据缺失必须 ask_user，禁止编造。
2. **选模板**：读 `references/panel-templates.md`，找到最接近的场景（文件编辑 / PPT 演示 / SSH 连接），在其基础上改，**不要凭空造轮子**。
3. **写代码**：先输出简要计划，再按模板写 `build_panel(owner)`（禁止 mock：控件、文件读写、网络连接必须真实实现）。
4. **注册**：调用 `register_feature_panel`：`name`（英文，`[A-Za-z0-9_-]`）、`title`、`width`、`height`、`python`（完整的 build_panel 代码）。它会写入当前工作流 panel.py 并触发界面重建。
5. **第三方依赖**：若代码 import 第三方库（如 paramiko），必须同时调用 `set_feature_deps` 声明（写入当前工作流 requirements.txt，加载时自动安装），并在界面里对库缺失给出可操作的提示，禁止静默失败。
6. **自查**：确认面板名/标题正确、未新建工作流（一律注入当前工作流）、占位与 mock 为零；任务结束向用户说明面板已挂载为独立浮窗。

## 三、硬性规范（必须遵守）
- 严禁 mock/占位数据/假控件：每个按钮、列表、输入框都必须有真实行为。
- 面板一律注入当前工作流（register_feature_panel 或 write_file 写 panel.py），禁止新建工作流 / UI/UX 资源包。
- SSH/网络类面板不保存明文密码到文件；连接必须在后台线程（QThread），禁止阻塞主界面。
- 界面遵循程序现有风格：不引入 emoji 图标；底色用程序主题变量（owner 提供）或纯黑+淡灰+白+深蓝的简约矢量风格。

## 四、模板与示例
完整的可运行实现见 `references/panel-templates.md`（文件编辑器 / PPT 演示 / SSH 连接 三个模板 + 参数说明）。找不到时按上方 §一 契约内联实现。