"""回归：主 Agent 派发子任务时必须携带必要上下文（prompt 强提示 + 运行时兜底注入）。"""
import os, sys, pathlib
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath("src"))
from PyQt6.QtWidgets import QApplication
app = QApplication([])
from winapp_migrator.core import agent_tools, agent_engine

# 1) 工具 schema：context 已强提示为“必须”（模型可见的成文约束）
schemas = agent_tools.tool_schemas(None)
ds = next(t for t in schemas if t.get("function", {}).get("name") == "dispatch_sub_agents")
desc = ds["function"]["description"]
assert "必须携带 context" in desc, "工具描述未强调必须携带 context"
tasks_item = next(p for p in ds["function"]["parameters"]["properties"]["tasks"].get("items", {}).get("properties", {}).values())
ctx_desc = ds["function"]["parameters"]["properties"]["tasks"]["items"]["properties"]["context"]["description"]
assert "必需" in ctx_desc and "独立" in ctx_desc, "context 字段未标记必需"
print("1) 工具 schema 强调『必须带 context』PASS")

# 2) SKILL.md 同时强调
skill = open(pathlib.Path(agent_engine.__file__).resolve().parents[1]
             / "skills" / "sub-agent" / "SKILL.md", encoding="utf-8").read()
assert "必须带 context" in skill, "sub-agent SKILL.md 未强调必须带 context"
print("2) sub-agent SKILL.md 强调 PASS")

# 3) 运行时兜底：缺 context 的任务自动注入主对话最近背景
eng = agent_engine.AgentEngine.__new__(agent_engine.AgentEngine)
eng._messages = [
    {"role": "system", "content": "你是主 Agent"},
    {"role": "user", "content": [{"type": "text", "text": "请帮我重构 main.py 的登录逻辑"},
                                 {"type": "image_url", "image_url": {"url": "data:x"}}]},
    {"role": "tool", "content": "[工具输出] 不应进入背景"},
    {"role": "assistant", "content": "我先读取 main.py 相关函数，再派发子任务。"},
]
bg = eng._auto_sub_context()
assert bg.startswith("主 Agent 对话最近背景"), f"背景前缀异常: {bg[:30]}"
assert "登录逻辑" in bg, "背景未包含用户请求"
assert "工具输出" not in bg, "工具结果混入背景"
assert "data:x" not in bg, "图片 data URL 混入背景"
assert "你是主 Agent" not in bg, "system 混入背景"
print("3) _auto_sub_context 摘取最近背景 PASS")
print("   ", bg.replace(chr(10), " | ")[:120], "…")

# 4) _dispatch_tasks 兜底：缺 context 任务被注入背景（agent_failed 短路，不触发真实派发）
eng = agent_engine.AgentEngine.__new__(agent_engine.AgentEngine)
eng._messages = [{"role": "user", "content": "分析与修复登录模块的性能问题"},
                 {"role": "assistant", "content": "正在派发子任务。"}]
eng.on_status = None
eng._stop = type("S", (), {"is_set": lambda self: False})()
t = {"title": "t1", "goal": "分析登录模块", "agent_failed": "None"}
out = eng._dispatch_tasks([t])
assert t.get("context") and "登录模块" in t["context"], "缺 context 任务未自动注入背景"
assert "【子任务 1】" in out, "汇总输出异常"
print("4) 缺 context 自动注入背景 PASS")

# 5) 自带 context 的任务不被覆盖
eng2 = agent_engine.AgentEngine.__new__(agent_engine.AgentEngine)
eng2._messages = [{"role": "user", "content": "x"}]
eng2.on_status = None
eng2._stop = type("S", (), {"is_set": lambda self: False})()
t2 = {"title": "t2", "goal": "g", "context": "显式上下文内容", "agent_failed": "None"}
eng2._dispatch_tasks([t2])
assert t2["context"] == "显式上下文内容", "显式 context 被覆盖"
print("5) 显式 context 不被覆盖 PASS")
print("ALL RESULT: PASS")