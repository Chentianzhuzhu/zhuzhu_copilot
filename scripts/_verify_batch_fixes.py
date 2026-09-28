"""四项修复的回归验证：
1) 子 Agent 人格不被内置通用系统提示覆盖，全链路（注册→读取→system 消息）生效；
2) 失联内置 UI/UX 包（如已删除的液态玻璃主题残留）自动清理、活跃包复位默认；
3) 模型回退：手动指定失败不回退；自动选择失败静默回退内置（不再弹提示）；
4) diff 预览：show_file 不再覆盖红绿高亮，3s 后恢复正常显示。
"""
import os, sys, tempfile, json, pathlib
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath("src"))
from PyQt6.QtWidgets import QApplication
app = QApplication([])
import zhuzhu_Copilot.core.agent_subagent as agent_subagent
import zhuzhu_Copilot.core.agent_ui_ux as agent_ui_ux
import zhuzhu_Copilot.core.agent_llm as agent_llm
from zhuzhu_Copilot.ui import agent_panel as ap

print("=== 1) 子 Agent 人格 ===")
persona = "你是一位资深教师，负责辅导用户学习 C 语言，请用耐心、循序渐进的方式讲解。"
# a) 系统提示：有 persona 时以 persona 开头、不含内置通用文本
sysp = agent_subagent._sub_system_prompt(persona)
assert sysp.startswith(persona), "系统提示未以 persona 开头"
assert "你是子 Agent" not in sysp and "结果会被主 Agent 汇总使用" not in sysp, \
    "内置通用规则文本仍混入 persona 系统提示"
# 无 persona：保持内置文本
assert "你是子 Agent" in agent_subagent._sub_system_prompt("")
# b) 注册 → 读取 → 工具映射全链路保留 persona（工作流目录指向临时目录）
tmpwf = pathlib.Path(tempfile.mkdtemp())
_orig_sf = agent_subagent._subagent_file
agent_subagent._subagent_file = lambda workflow="": tmpwf / "subagents.json"
try:
    wf = "default"
    name = "verify_tutor"
    ok, msg = agent_subagent.register_subagent(name, "测试", "讲一个知识点",
                                               "read_file", persona, workflow=wf)
    assert ok, f"注册失败: {msg}"
    conf = agent_subagent.subagent_tool(name, wf)
    assert conf and conf.get("persona") == persona, "subagent_tool 未返回 persona"
    items = agent_subagent.registered_subagents(wf)
    assert any(it["name"] == name and it["persona"] == persona for it in items), \
        "registered_subagents 未返回 persona"
finally:
    agent_subagent._subagent_file = _orig_sf
# c) run_sub_agent 首条 system 消息用 persona（stub LLM 记录 messages）
seen = {}
class FakeLLM:
    def chat_stream(self, messages, **kw):
        seen["system"] = next(m["content"] for m in messages if m["role"] == "system")
        return {"text": "ok", "tool_calls": []}
agent_subagent.run_sub_agent(FakeLLM(), "任务", persona=persona)
assert str(seen["system"]).startswith(persona), "run_sub_agent 未用 persona 作为 system"
assert "我是子 Agent" not in str(seen["system"]), "run_sub_agent 仍注入内置通用文本"
# d) 自定义子 Agent（无显式 persona）：system 不含「你是子 Agent」身份声明
neutral = agent_subagent._sub_system_prompt("", custom=True)
assert "你是子 Agent" not in neutral and "结果会被主 Agent 汇总使用" not in neutral, \
    "custom 子 Agent 仍被通用身份文本覆盖"
agent_subagent.run_sub_agent(FakeLLM(), "你是谁", custom=True)
assert "你是子 Agent" not in str(seen["system"]), "custom 子 Agent system 仍注入通用身份"
# e) 注册 goal 兜底人格（无 persona 字段时）：@子Agent 直接调用以 goal 为系统提示
goal_persona = "你是一名耐心的学习辅导老师，负责讲解知识点与答疑解惑。"
agent_subagent.run_sub_agent(FakeLLM(), "你是谁", persona=goal_persona, custom=True)
assert str(seen["system"]).startswith(goal_persona), "goal 未作为 persona 注入系统提示"
assert "你是子 Agent" not in str(seen["system"]), "goal 人格被通用身份文本覆盖"
print("1) PASS")

print("=== 2) 失联内置 UI/UX 包清理 ===")
tmp = pathlib.Path(tempfile.mkdtemp())
src_root = tmp / "builtin"; src_root.mkdir()
usr_root = tmp / "uiux"; usr_root.mkdir()
act = tmp / "active.txt"; act.write_text("old_theme", encoding="utf-8")
_orig_dir, _orig_file = agent_ui_ux._UI_UX_DIR, agent_ui_ux._ACTIVE_FILE
_orig_bd = agent_ui_ux._builtin_packages_dir
try:
    agent_ui_ux._UI_UX_DIR = usr_root
    agent_ui_ux._ACTIVE_FILE = act
    agent_ui_ux._builtin_packages_dir = lambda: src_root
    # 造一份「已下架内置包」残留：is_builtin=True 且不再随应用分发
    stale = usr_root / "liquid_glass_v2_BETA"
    stale.mkdir()
    (stale / "ui_ux.json").write_text(json.dumps({"name": "liquid_glass_v2_BETA", "is_builtin": True}),
                                      encoding="utf-8")
    # 造一份用户自建包：is_builtin 非真，不得被清理
    custom = usr_root / "my_custom"
    custom.mkdir()
    (custom / "ui_ux.json").write_text(json.dumps({"name": "my_custom", "is_builtin": False}),
                                       encoding="utf-8")
    agent_ui_ux._invalidate_active_cache()
    agent_ui_ux._purge_stale_builtin_packages()
    assert not stale.exists(), "失联内置包未清理"
    assert custom.exists(), "用户自建包被误删"
    assert agent_ui_ux.get_active_package() == agent_ui_ux.DEFAULT_PACKAGE, "活跃包未复位默认"
    print("1) 失联内置包已清、自建包保留、活跃包复位 default")
    # 内置 default 包缺失时 _ensure_dir 能补全且不报错（走真实路径前先换回临时）
    usr_root.mkdir(exist_ok=True)
    agent_ui_ux._ensure_dir()
    assert (usr_root / "default" / "ui_ux.json").is_file(), "_ensure_dir 未生成默认包"
    print("2) PASS")
finally:
    agent_ui_ux._UI_UX_DIR, agent_ui_ux._ACTIVE_FILE = _orig_dir, _orig_file
    agent_ui_ux._builtin_packages_dir = _orig_bd
    agent_ui_ux._invalidate_active_cache()

print("=== 3) 模型回退策略 ===")
class FailStream:
    def __init__(self, fail=0):
        self.fail = fail
    def __call__(self, *a, **k):
        if self.fail > 0:
            self.fail -= 1
            raise agent_llm.AgentLLMError("上游 502 不可用")
        return {"text": "fallback-ok", "tool_calls": [], "usage": None}
try:
    # a) 手动指定模型（auto_fallback=False）：失败直接抛错，不回退
    c1 = agent_llm.LLMClient(model="deepseek-v4")
    c1.auto_fallback = False
    c1._chat_stream_once = FailStream(fail=1)
    try:
        c1.chat_stream([{"role": "user", "content": "hi"}])
        raise AssertionError("auto_fallback=False 未抛错")
    except agent_llm.AgentLLMError:
        pass
    assert not c1.fell_back, "指定模型失败不应 fall_back"
    print("  a) 手动指定失败直接报错 PASS")
    # b) 自动选择（auto_fallback=True + silent）：静默回退内置，fell_back 置位
    c2 = agent_llm.LLMClient(model="deepseek-v4")
    c2.auto_fallback = True
    c2.silent_fallback = True
    c2._chat_stream_once = FailStream(fail=1)
    out = c2.chat_stream([{"role": "user", "content": "hi"}])
    assert out["text"] == "fallback-ok" and c2.fell_back, "自动选择静默回退未生效"
    assert c2.model == "deepseek-v4", "回退后连接参数未恢复"
    print("  b) 自动选择静默回退 + 参数恢复 PASS")
finally:
    pass
print("3) PASS")

print("=== 4) diff 预览不被 show_file 覆盖 ===")
tmpf = pathlib.Path(tempfile.mkdtemp()) / "demo.py"
new_content = "def add(a, b):\n    return a + b\n"
old_content = "def add(a, b):\n    # 旧实现\n    return a - b\n"
tmpf.write_text(new_content, encoding="utf-8")
win = ap.CodePreviewWindow()
win.setFixedWidth(ap.CodePreviewWindow.WIDTH)
win.show_file(str(tmpf))
win.show_diff(str(tmpf), old_content, new_content)
body = win.text.toPlainText()
assert "+     return a + b" in body and "-     return a - b" in body and "-     # 旧实现" in body, \
    "diff 行内容缺失"
assert len(win.text.extraSelections()) >= 3, "红绿高亮选区缺失"
assert win._diff_path == str(tmpf) and win._diff_until > 0, "diff 保护窗口未设置"
# 模拟引擎 preview 事件重刷同文件：不得覆盖 diff
win.show_file(str(tmpf))
assert win._diff_path == str(tmpf) and win._diff_until > 0, "show_file 覆盖了 diff 保护"
assert "+     return a + b" in win.text.toPlainText(), "同文件自动预览覆盖了红绿 diff"
print("  展示期 show_file 被拦截、diff 保留 PASS")
# 恢复：3s 后恢复正常显示（磁盘当前内容 + 语法高亮）
win._restore_text_view(str(tmpf), win._diff_seq)
assert win._diff_until == 0 and win._diff_path == "", "恢复后保护标志未清除"
assert "return a + b" in win.text.toPlainText() and "# 旧实现" not in win.text.toPlainText(), "恢复未回到磁盘内容"
print("4) PASS")
print("ALL RESULT: PASS")