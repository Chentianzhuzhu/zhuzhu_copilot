"""源码级插件引擎回归：五类 op、多模块集成、路径防护、备份/回滚、校验闸门。

真实执行的部分（不是纸面断言）：
  · 在临时「源码根」里**真写真改真编译**（py_compile），真备份、真回滚；
  · 路径穿越/绝对路径/受保护核心程序真拒绝；锚点缺失真报错并整体回滚；
  · 危险 op 在应用前被插件校验系统整体删除插件包（源码一行未动）。

LLM 评审用注入替身（测试环境不发起真实网络请求）。
"""

import json

import pytest

from zhuzhu_Copilot.core import (agent_plugins, agent_skills, plugin_guard,
                                 plugin_source)

# 目标文件桩：含工具清单锚点、现有函数区、文件尾标记
TOOLS_PY = '''"""agent_tools 桩"""

TOOLS = [
    {"name": "read_file"},
]

# --- 现有函数区 ---
def _existing():
    return "old"


# --- 文件尾标记 ---
# END
'''


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """隔离插件目录/技能配置；把「应用源码根」指到临时目录；LLM 替身默认安全"""
    monkeypatch.setattr(agent_plugins, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(agent_skills, "CONFIG_DIR", tmp_path / "agent")
    src = tmp_path / "src"
    (src / "zhuzhu_Copilot" / "core").mkdir(parents=True)
    monkeypatch.setattr(plugin_source, "app_source_root", lambda: src)
    monkeypatch.setattr(plugin_guard, "_llm_review",
                        lambda blobs: {"verdict": "safe", "reason": "测试替身：安全"})
    agent_skills._MCP_CACHE["data"] = None
    return src


def _target(src, rel="zhuzhu_Copilot/core/agent_tools.py", text=TOOLS_PY):
    p = src / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _mk_source_plugin(name, ops, **over):
    d = agent_plugins.plugin_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    meta = {"name": name, "kind": "source", "enabled": True,
            "description": "t", "author": "测试作者"}
    meta.update(over)
    (d / "plugin.json").write_text(json.dumps(meta, ensure_ascii=False),
                                   encoding="utf-8")
    (d / "source_ops.json").write_text(json.dumps(ops, ensure_ascii=False),
                                       encoding="utf-8")
    return d


# ══════════ 1. 五类 op 真实应用 ══════════

def test_all_op_kinds_apply(iso):
    """create_file / append / insert_after / replace_module 一次应用多文件多 op"""
    src = iso
    _target(src)
    ops = [
        {"op": "create_file", "path": "zhuzhu_Copilot/core/my_mod.py",
         "content": "def hello():\n    return 'hi'\n"},
        {"op": "insert_after", "path": "zhuzhu_Copilot/core/agent_tools.py",
         "anchor": '"name": "read_file"',
         "content": '{"name": "my_tool"},'},
        {"op": "append", "path": "zhuzhu_Copilot/core/agent_tools.py",
         "after": "# --- 文件尾标记 ---",
         "content": "def tail_tool():\n    return 1"},
        {"op": "replace_module", "path": "zhuzhu_Copilot/core/agent_tools.py",
         "start": "def _existing():", "end": 'return "old"',
         "content": "def _existing():\n    return 'new'"},
    ]
    _mk_source_plugin("p1", ops)
    ok, msg = plugin_source.apply_source_plugin("p1")
    assert ok, msg

    new_mod = (src / "zhuzhu_Copilot" / "core" / "my_mod.py")
    assert new_mod.is_file() and "def hello" in new_mod.read_text(encoding="utf-8")

    text = (src / "zhuzhu_Copilot" / "core" / "agent_tools.py").read_text(encoding="utf-8")
    # insert_after：新工具条目紧跟 read_file 之后
    assert '"name": "my_tool"' in text
    assert text.index('"name": "my_tool"') > text.index('"name": "read_file"')
    # append：尾部落入文件尾标记之后
    assert "def tail_tool" in text
    assert text.index("def tail_tool") > text.index("# --- 文件尾标记 ---")
    # replace_module：旧函数体被替换
    assert 'return "old"' not in text and "return 'new'" in text
    # 编译检查通过（apply 内部已逐文件 py_compile）
    st = plugin_source.source_plugin_status("p1")
    assert st["applied"] and st["ops"] == 4 and len(st["files"]) == 2


def test_replace_file_and_multi_module_integration(iso):
    """replace_file 整体替换 + 多文件综合修改（一次插件改两个关联模块）"""
    src = iso
    _target(src, "zhuzhu_Copilot/core/agent_tools.py")
    _target(src, "zhuzhu_Copilot/core/agent_engine.py", "# engine stub\n")
    ops = [
        {"op": "replace_file", "path": "zhuzhu_Copilot/core/agent_engine.py",
         "content": "# engine v2\nVALUE = 2\n"},
        {"op": "append", "path": "zhuzhu_Copilot/core/agent_tools.py",
         "content": "HOOK = 'engine-v2'"},
    ]
    _mk_source_plugin("p2", ops)
    ok, msg = plugin_source.apply_source_plugin("p2")
    assert ok, msg
    assert "VALUE = 2" in (src / "zhuzhu_Copilot" / "core" /
                           "agent_engine.py").read_text(encoding="utf-8")
    assert "HOOK" in (src / "zhuzhu_Copilot" / "core" /
                      "agent_tools.py").read_text(encoding="utf-8")


def test_load_source_ops_prefers_plugin_json_field(iso):
    """source_ops 写在 plugin.json 里也能读（两种载体都支持）"""
    d = _mk_source_plugin("p3", [])
    ops = [{"op": "create_file", "path": "a.py", "content": "x = 1\n"}]
    (d / "plugin.json").write_text(json.dumps(
        {"name": "p3", "kind": "source", "source_ops": ops}, ensure_ascii=False),
        encoding="utf-8")
    assert plugin_source.load_source_ops("p3") == ops


# ══════════ 2. 路径与结构防护 ══════════

@pytest.mark.parametrize("bad_path,why", [
    ("/etc/passwd", "相对路径"),
    ("../outside.py", "穿越"),
    ("zhuzhu_Copilot/core/../../evil.py", "穿越"),
    ("zhuzhu_Copilot/core/plugin_guard.py", "受保护"),
    ("zhuzhu_Copilot/core/security_engine/engine.py", "受保护"),
])
def test_rejects_illegal_paths(iso, bad_path, why):
    src = iso
    _target(src)
    before = (src / "zhuzhu_Copilot" / "core" / "agent_tools.py").read_text(encoding="utf-8")
    ops = [{"op": "create_file", "path": bad_path, "content": "x = 1\n"}]
    _mk_source_plugin("bad", ops)
    ok, msg = plugin_source.apply_source_plugin("bad")
    assert ok is False, msg
    assert why in msg, msg
    # 源码未被改动
    assert (src / "zhuzhu_Copilot" / "core" / "agent_tools.py").read_text(
        encoding="utf-8") == before
    assert not (src / "evil.py").exists() and not (src / "outside.py").exists()


def test_create_file_refuses_overwrite(iso):
    """create_file 不覆盖已存在文件（防误伤），需显式 replace_file"""
    src = iso
    _target(src)
    _mk_source_plugin("ov", [{"op": "create_file",
                              "path": "zhuzhu_Copilot/core/agent_tools.py",
                              "content": "x = 1\n"}])
    ok, msg = plugin_source.apply_source_plugin("ov")
    assert ok is False and "拒绝覆盖" in msg
    assert "agent_tools 桩" in (src / "zhuzhu_Copilot" / "core" /
                                "agent_tools.py").read_text(encoding="utf-8")


def test_validate_source_ops_rejects_bad_structures():
    ok, why = plugin_source.validate_source_ops([])
    assert ok is False and "为空" in why
    ok, why = plugin_source.validate_source_ops([{"op": "drop_table", "path": "a"}])
    assert ok is False and "未知" in why
    ok, why = plugin_source.validate_source_ops([{"op": "append", "path": "a.py"}])
    assert ok is False and "content" in why


# ══════════ 3. 失败回滚 ══════════

def test_missing_anchor_rolls_back_everything(iso):
    """锚点缺失 → 整包回滚：已建的新文件删除、已改文件还原，源码零改动"""
    src = iso
    _target(src)
    before = (src / "zhuzhu_Copilot" / "core" / "agent_tools.py").read_text(encoding="utf-8")
    ops = [
        {"op": "create_file", "path": "zhuzhu_Copilot/core/ok_mod.py",
         "content": "x = 1\n"},
        {"op": "insert_after", "path": "zhuzhu_Copilot/core/agent_tools.py",
         "anchor": "NO_SUCH_ANCHOR_12345", "content": "y = 2"},
    ]
    _mk_source_plugin("anchor_bad", ops)
    ok, msg = plugin_source.apply_source_plugin("anchor_bad")
    assert ok is False and "找不到锚点" in msg and "回滚" in msg
    assert not (src / "zhuzhu_Copilot" / "core" / "ok_mod.py").exists(), \
        "回滚必须删掉已创建的新文件"
    assert (src / "zhuzhu_Copilot" / "core" / "agent_tools.py").read_text(
        encoding="utf-8") == before, "回滚必须还原被改文件"


def test_compile_failure_rolls_back_everything(iso):
    """应用后编译不过（语法错误）→ 整体回滚，不留半成品"""
    src = iso
    _target(src)
    before = (src / "zhuzhu_Copilot" / "core" / "agent_tools.py").read_text(encoding="utf-8")
    ops = [
        {"op": "append", "path": "zhuzhu_Copilot/core/agent_tools.py",
         "content": "def broken(:\n    pass"},
    ]
    _mk_source_plugin("syntax_bad", ops)
    ok, msg = plugin_source.apply_source_plugin("syntax_bad")
    assert ok is False and "编译检查未通过" in msg and "回滚" in msg
    assert (src / "zhuzhu_Copilot" / "core" / "agent_tools.py").read_text(
        encoding="utf-8") == before


# ══════════ 4. 应用 → 卸载回滚 ══════════

def test_apply_then_revert_roundtrip(iso):
    """应用后 revert：新建的文件删除、修改的文件还原、清单清除"""
    src = iso
    _target(src)
    before = (src / "zhuzhu_Copilot" / "core" / "agent_tools.py").read_text(encoding="utf-8")
    ops = [
        {"op": "create_file", "path": "zhuzhu_Copilot/core/round_mod.py",
         "content": "z = 3\n"},
        {"op": "append", "path": "zhuzhu_Copilot/core/agent_tools.py",
         "content": "HOOK = 1"},
    ]
    _mk_source_plugin("rt", ops)
    assert plugin_source.apply_source_plugin("rt")[0]
    assert (agent_plugins.plugin_dir("rt") / "applied.json").is_file()
    ok, msg = plugin_source.revert_source_plugin("rt")
    assert ok, msg
    assert not (src / "zhuzhu_Copilot" / "core" / "round_mod.py").exists()
    assert (src / "zhuzhu_Copilot" / "core" / "agent_tools.py").read_text(
        encoding="utf-8") == before
    assert not (agent_plugins.plugin_dir("rt") / "applied.json").exists()
    assert plugin_source.source_plugin_status("rt")["applied"] is False


def test_revert_skips_user_modified_file(iso):
    """应用后被用户手动改过的文件：revert 跳过不覆盖，并明确注明"""
    src = iso
    _target(src)
    _mk_source_plugin("um", [{"op": "append",
                              "path": "zhuzhu_Copilot/core/agent_tools.py",
                              "content": "HOOK = 1"}])
    assert plugin_source.apply_source_plugin("um")[0]
    t = src / "zhuzhu_Copilot" / "core" / "agent_tools.py"
    t.write_text(t.read_text(encoding="utf-8") + "\n# 用户后来加的\n", encoding="utf-8")
    ok, msg = plugin_source.revert_source_plugin("um")
    assert ok and "已被修改" in msg
    assert "用户后来加的" in t.read_text(encoding="utf-8"), \
        "不得覆盖用户应用后的手动修改"


def test_revert_restores_original_after_multi_ops_same_file(iso):
    """同一文件被多条 op 修改：备份必须是应用前原文，revert 还原到原始内容
    （而不是中间态——曾因后一条 op 覆盖备份而还原不干净）。"""
    src = iso
    _target(src)
    before = (src / "zhuzhu_Copilot" / "core" / "agent_tools.py").read_text(encoding="utf-8")
    ops = [
        {"op": "insert_after", "path": "zhuzhu_Copilot/core/agent_tools.py",
         "anchor": '"name": "read_file"', "content": '{"name": "t1"},'},
        {"op": "append", "path": "zhuzhu_Copilot/core/agent_tools.py",
         "content": "HOOK = 1"},
        {"op": "append", "path": "zhuzhu_Copilot/core/agent_tools.py",
         "content": "HOOK2 = 2"},
    ]
    _mk_source_plugin("multi_same", ops)
    assert plugin_source.apply_source_plugin("multi_same")[0]
    text = (src / "zhuzhu_Copilot" / "core" / "agent_tools.py").read_text(encoding="utf-8")
    assert '"name": "t1"' in text and "HOOK = 1" in text and "HOOK2 = 2" in text
    ok, msg = plugin_source.revert_source_plugin("multi_same")
    assert ok, msg
    assert (src / "zhuzhu_Copilot" / "core" / "agent_tools.py").read_text(
        encoding="utf-8") == before, "同文件多 op 后 revert 必须还原到原始内容"


def test_revert_without_applied_manifest(iso):
    _mk_source_plugin("never", [{"op": "create_file", "path": "a.py",
                                 "content": "x = 1\n"}])
    ok, msg = plugin_source.revert_source_plugin("never")
    assert ok is False and "没有已应用" in msg


# ══════════ 5. 校验闸门：危险源码插件应用前整体删除 ══════════

def test_dangerous_source_plugin_blocked_before_apply(iso):
    """危险 op 在应用前被校验系统拦下：插件包整体删除，源码一行未动"""
    src = iso
    _target(src)
    before = (src / "zhuzhu_Copilot" / "core" / "agent_tools.py").read_text(encoding="utf-8")
    d = _mk_source_plugin("evil_apply", [
        {"op": "append", "path": "zhuzhu_Copilot/core/agent_tools.py",
         "content": "import os\nos.system('calc')\nHOOK = 1"}])
    ok, msg = plugin_source.apply_source_plugin("evil_apply")
    assert ok is False and "强制删除" in msg
    assert not d.exists(), "危险源码插件包必须被整体删除"
    assert (src / "zhuzhu_Copilot" / "core" / "agent_tools.py").read_text(
        encoding="utf-8") == before, "源码不允许被危险插件改动"


def test_llm_dangerous_source_plugin_blocked(iso, monkeypatch):
    """LLM 判危（无静态命中）同样拦下并整体删除插件包"""
    monkeypatch.setattr(plugin_guard, "_llm_review",
                        lambda blobs: {"verdict": "dangerous",
                                       "reason": "伪装的数据收集器"})
    src = iso
    _target(src)
    d = _mk_source_plugin("evil_llm", [
        {"op": "append", "path": "zhuzhu_Copilot/core/agent_tools.py",
         "content": "HOOK = 1"}])
    ok, msg = plugin_source.apply_source_plugin("evil_llm")
    assert ok is False and "LLM" in msg
    assert not d.exists()


# ══════════ 6. AI 创建链路（替身设计） ══════════

def test_create_source_plugin_from_nl_creates_package(iso, monkeypatch):
    """AI 设计 → 落盘插件包（含元数据）→ 过校验 → 不自动改源码"""
    spec = {"name": "habit_core", "summary": "习惯打卡模块", "author": "某公司",
            "source_ops": [
                {"op": "create_file", "path": "zhuzhu_Copilot/core/habit.py",
                 "content": "def punch():\n    return 'ok'\n"},
                {"op": "append", "path": "zhuzhu_Copilot/core/agent_tools.py",
                 "content": "HABIT_HOOK = punch"}]}
    monkeypatch.setattr(agent_plugins, "_ai_generate_source_spec",
                        lambda desc: spec)
    ok, msg = agent_plugins.create_source_plugin_from_nl("做个习惯打卡模块")
    assert ok, msg
    d = agent_plugins.plugin_dir("habit_core")
    meta = json.loads((d / "plugin.json").read_text(encoding="utf-8"))
    assert meta["kind"] == "source" and meta["author"] == "某公司"
    assert meta.get("updated_at")
    assert (d / "source_ops.json").is_file()
    assert "尚未改动源码" in msg and "apply_source_plugin" in msg
    # 源码确实未被改动
    assert not (iso / "zhuzhu_Copilot" / "core" / "habit.py").exists()


def test_create_source_plugin_rejects_dangerous_design(iso, monkeypatch):
    """AI 设计了危险代码 → 校验系统强制删除，创建失败并提示"""
    spec = {"name": "evil_ai", "summary": "x", "author": "",
            "source_ops": [
                {"op": "create_file", "path": "zhuzhu_Copilot/core/evil.py",
                 "content": "import winreg\nwinreg.SetValue(1, 2, 3)\n"}]}
    monkeypatch.setattr(agent_plugins, "_ai_generate_source_spec",
                        lambda desc: spec)
    ok, msg = agent_plugins.create_source_plugin_from_nl("做个后门")
    assert ok is False and "强制删除" in msg
    assert not agent_plugins.plugin_dir("evil_ai").exists()
