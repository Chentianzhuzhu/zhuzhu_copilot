"""回归验证：todos 面板高度与内容精确匹配（pre-show 阶段无失真 → 无底部文字挤压/底部留白）。
修复前：构造期面板宽度为瞬时中间值（约172），导致 pre-show 估算失真、面板高度钉死错误，
既可能挤压底部文字（估算偏小）也可能留大片空白（估算偏大）。
修复后：测量宽度取窗口固定宽度，pre-show 估算 = show 后真实值。
"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "windows")
sys.path.insert(0, os.path.abspath("src"))
from PyQt6.QtWidgets import QApplication, QLabel
app = QApplication([])
from zhuzhu_Copilot.ui import agent_panel as ap

todos = [
    {"title": "分析用户需求，制定学习计划框架，需要注意各个阶段的衔接与节奏把控，并给出可执行的排期", "status": "in_progress"},
    {"title": "生成C语言入门学习文档（Word），包含全部示例代码、注释说明与运行效果截图", "status": "pending"},
    {"title": "生成C语言练习题集（Excel），按章节与难度分级组织题目并附带答案解析", "status": "pending"},
    {"title": "总结学习建议并回复用户，附上后续提升路线的完整建议与资料清单", "status": "completed"},
]
win = ap.TodosWindow()
win.update_todos(todos)   # 模拟 AI 在任务启动早期（窗口尚未定位/布局）写入 todos
pre_est = win.panel._content_height()
pre_h = win.panel.height()
win.show()
for _ in range(8):
    app.processEvents()
post_est = win.panel._content_height()
post_h = win.panel.height()

print(f"pre-show est={pre_est} panel_h={pre_h}")
print(f"show 后 est={post_est} panel_h={post_h}")

# 1) pre-show 与 show 后估算一致（测量确定，不再随布局时机漂移）
assert abs(pre_est - post_est) <= 4, f"pre-show 估算失真: {pre_est} vs {post_est}"
# 2) 面板高度 = 固定换算(56) + 内容高度（无死区、无挤压）
assert post_h == 56 + post_est, f"面板高度与内容不匹配: {post_h} != {56 + post_est}"
# 3) 每行标签文本高度充足（无裁剪）
for i in range(win.panel._list_lay.count() - 1):
    w = win.panel._list_lay.itemAt(i).widget()
    lbl = w.findChild(QLabel, "todoTitle")
    need = lbl.heightForWidth(lbl.width())
    assert need <= lbl.height() + 2, f"row{i} 文本被挤压 need={need} > h={lbl.height()}"

# 4) 空占位也正常（高度 >= 占位控件高度，无挤压）
win.update_todos([])
for _ in range(3):
    app.processEvents()
ph = win.panel._list_lay.itemAt(0).widget()
assert win.panel.height() >= win.panel._list_lay.itemAt(0).widget().minimumHeight() + 50, \
    "空占位被挤压"
print("RESULT: PASS")