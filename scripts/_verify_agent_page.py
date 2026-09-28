"""验证：Agent 管理页（主 Agent + 子 Agent）+ 定位器淡灰 + 输入框提示。"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath("src"))
from PyQt6.QtWidgets import QApplication, QComboBox, QListWidget, QLabel
app = QApplication([])
from zhuzhu_Copilot.ui import agent_panel as ap

src = open(os.path.join(os.path.dirname(ap.__file__), "agent_panel.py"),
           encoding="utf-8").read()

# 1. 需求2：输入框提示文字
assert "/ for command @ for agent" in src, "输入框提示文字未改"
print("1. 输入框提示文字 / for command @ for agent: OK")

# 2. 需求1：定位器圆点淡灰
assert "rgba(170,175,185,130)" in src, "定位器圆点未改淡灰"
print("2. 定位器圆点淡灰色: OK")

# 3. 需求3：Agent 管理页存在 + 导航/构建器接入
assert "def _build_agent_page" in src, "Agent 管理页缺失"
assert '"Agent 管理", "user"' in src, "导航未接入 Agent 管理"
assert "self._build_agent_page," in src, "页面构建器未接入"
print("3. Agent 管理页 + 导航接入: OK")

# 4. Agent 页核心逻辑（stub 验证刷新/注册 API 调用路径）
d = ap._AgentSettingsDialog.__new__(ap._AgentSettingsDialog)
d.agent_wf = QComboBox()
d.agent_sub_list = QListWidget()
d.agent_main_info = QLabel()
d._reload_agent_view()
assert d.agent_wf.count() >= 1, "对话流下拉未填充"
print("4a. 对话流下拉填充:", d.agent_wf.count(), "个")
assert "主 Agent" in d.agent_main_info.text(), "主 Agent 信息未显示"
print("4b. 主 Agent 信息:", d.agent_main_info.text().replace(chr(10), " | "))
print("4c. 子 Agent 列表:", d.agent_sub_list.count(), "条（真实读取）")

# 注册/删除 API 签名可调用（隔离目录模拟）
import tempfile
import pathlib
import zhuzhu_Copilot.core.agent_subagent as submod
_tmp = tempfile.mkdtemp()
_tf = pathlib.Path(_tmp) / "subagents.json"
submod._subagent_file = lambda workflow="": _tf
ok, msg = submod.register_subagent("sub_reviewer", "审查", "审查代码并给出建议",
                                   "read_file", workflow="t1")
assert ok, f"注册失败: {msg}"
subs = submod.registered_subagents("t1")
assert any(x["name"] == "sub_reviewer" for x in subs), "注册后未列出"
ok2, _ = submod.unregister_subagent("sub_reviewer", workflow="t1")
assert ok2, "删除失败"
assert not any(x["name"] == "sub_reviewer" for x in submod.registered_subagents("t1")), "删除后仍在"
print("5. 子 Agent 注册/列出/删除: OK")

print("SMOKE OK")
