"""插件校验系统回归：静态规则 + LLM 评审 + 危险强制删除 + 受保护核心程序。

真实执行的部分（不是纸面断言）：
  · 静态规则对真实源码文本匹配（eval/exec、os.system、注册表、持久化、
    凭据窃取、混淆载荷、篡改校验程序等）；
  · guard_plugin 对真实落盘的插件目录判罚并**真删除**危险文件、写校验日志；
  · import_plugin_zip 集成：危险插件包被拒绝导入，且危险文件已被强制删除。

LLM 评审一律用注入替身（测试环境不发起真实网络请求），覆盖三条路径：
LLM 判危 / LLM 判安全 / LLM 不可用（降级为纯规则判定并注明）。
"""

import json
import zipfile

import pytest

from zhuzhu_Copilot.core import agent_plugins, agent_skills, plugin_guard


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """隔离插件目录与技能配置；LLM 评审替身（逐例可覆盖）"""
    monkeypatch.setattr(agent_plugins, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(agent_skills, "CONFIG_DIR", tmp_path / "agent")
    agent_skills._MCP_CACHE["data"] = None
    agent_skills.invalidate_skills_cache()
    monkeypatch.setattr(plugin_guard, "_llm_review",
                        lambda blobs: {"verdict": "safe", "reason": "测试替身：安全"})
    return tmp_path


def _mk_plugin(name, files):
    """落盘一个插件目录（files 可含 plugin.json，缺省自动补）"""
    d = agent_plugins.plugin_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    if "plugin.json" not in files:
        (d / "plugin.json").write_text(json.dumps(
            {"name": name, "kind": "mcp", "enabled": True, "description": "t"},
            ensure_ascii=False), encoding="utf-8")
    return d


SAFE_SERVER = ('"""MCP server 桩"""\n'
               'def tool_echo(args):\n    return str(args.get("t", ""))\n')

# ══════════ 1. 静态规则 ══════════

def test_static_rules_hit_danger_patterns():
    """危险模式必须命中 danger 级规则（点名规则，防止规则被悄悄删掉）"""
    cases = {
        "dynamic_exec": "payload = '1+1'\neval(payload)\n",
        "os_system": "import os\nos.system('calc')\n",
        "shell_true": "import subprocess\nsubprocess.run('x', shell=True)\n",
        "registry": "import winreg\nwinreg.SetValue(k, n, v)\n",
        "persistence": "schtasks /create /tn evil /tr x\n",
        "credential": "data = open('Login Data','rb').read()\n",
        "mass_delete": "import shutil\nshutil.rmtree('C:\\\\Users')\n",
        "guard_tamper": "open('plugin_guard.py','w').write('x')\n",
    }
    for rule, code in cases.items():
        hits = plugin_guard.static_scan(code, "evil.py")
        got = {f["rule"] for f in hits if f["severity"] == "danger"}
        assert rule in got, f"{code!r} 未命中 {rule}（命中：{got}）"


def test_static_rules_clean_code_has_no_danger():
    """正常插件代码不得误报 danger（warn 级如 requests.post 允许存在，交 LLM 裁定）"""
    hits = plugin_guard.static_scan(SAFE_SERVER, "server.py")
    assert [f for f in hits if f["severity"] == "danger"] == [], hits


def test_protected_core_files():
    """校验系统自身、源码引擎、Copilot 插件系统、覆盖层钩子与应用入口受保护；
    普通模块不受保护"""
    for p in ("zhuzhu_Copilot/core/plugin_guard.py",
              "zhuzhu_Copilot/core/security_engine/engine.py",
              "zhuzhu_Copilot/core/security.py",
              "zhuzhu_Copilot/core/plugin_source.py",
              "zhuzhu_Copilot/core/copilot_plugins.py",
              "_copilot_overlay_hook.py",
              "main.py"):
        assert plugin_guard.is_protected_path(p), p
    assert not plugin_guard.is_protected_path("zhuzhu_Copilot/core/agent_tools.py")
    assert not plugin_guard.is_protected_path("zhuzhu_Copilot/ui/agent_panel.py")


# ══════════ 2. LLM 评审三条路径 ══════════

def test_guard_blobs_llm_dangerous(iso, monkeypatch):
    """无静态硬命中但 LLM 判危 → 判危险（llm_used=True）"""
    monkeypatch.setattr(plugin_guard, "_llm_review",
                        lambda blobs: {"verdict": "dangerous",
                                       "reason": "server.py 外发凭据"})
    ok, msg, detail = plugin_guard.guard_blobs(
        {"server.py": SAFE_SERVER}, plugin="p")
    assert ok is False and "强制删除" in msg
    assert detail["dangerous_files"] == {"server.py": detail["dangerous_files"]["server.py"]}
    assert "LLM 源码评审判定危险" in detail["dangerous_files"]["server.py"]
    assert detail["llm_used"] is True


def test_guard_blobs_llm_safe(iso):
    """LLM 判安全 → 通过，且注明用了 LLM 深度评审"""
    ok, msg, detail = plugin_guard.guard_blobs(
        {"server.py": SAFE_SERVER}, plugin="p")
    assert ok is True and "LLM 源码评审" in msg
    assert detail["llm_used"] is True


def test_guard_blobs_llm_unavailable_falls_back_to_rules(iso, monkeypatch):
    """LLM 不可用（返回 {}）→ 降级纯规则判定，结论里如实注明，不静默放行"""
    monkeypatch.setattr(plugin_guard, "_llm_review", lambda blobs: {})
    ok, msg, detail = plugin_guard.guard_blobs(
        {"server.py": SAFE_SERVER}, plugin="p")
    assert ok is True
    assert detail["llm_used"] is False
    assert "LLM 不可用" in msg


def test_guard_blobs_static_danger_skips_llm(iso, monkeypatch):
    """静态规则已判危险 → 不再送 LLM（省一次往返），消息注明判定来源"""
    calls = []
    monkeypatch.setattr(plugin_guard, "_llm_review",
                        lambda blobs: calls.append(1) or {"verdict": "safe"})
    ok, msg, detail = plugin_guard.guard_blobs(
        {"server.py": "import os\nos.system('x')\n"}, plugin="p")
    assert ok is False and "静态规则" in msg
    assert calls == [], "静态已判危险时不应调用 LLM"
    assert detail["llm_used"] is False


# ══════════ 3. guard_plugin：强制删除与提示 ══════════

def test_guard_plugin_keeps_entry_file_and_disables(iso):
    """入口文件（server.py）判危险时**不删文件**，改为停用插件并明确告知"""
    d = _mk_plugin("evil1", {"server.py": "import os\nos.system('calc')\n",
                             "SKILL.md": "---\nname: evil1\ndescription: t\n---\n"})
    ok, msg = plugin_guard.guard_plugin("evil1")
    assert ok is False
    assert "停用插件" in msg and "server.py" in msg and "os.system" in msg
    assert (d / "server.py").is_file(), "入口文件不得删除"
    assert (d / "SKILL.md").is_file(), "无关文件不受牵连"
    meta = json.loads((d / "plugin.json").read_text(encoding="utf-8"))
    assert meta.get("enabled") is False, "危险插件必须被停用"
    events = plugin_guard.recent_guard_events()
    assert events and events[0]["action"] == "force_delete"


def test_guard_plugin_deletes_non_entry_dangerous_file(iso):
    """非入口的危险文件仍然真删除（入口文件才享受保留+停用）"""
    d = _mk_plugin("evil2", {"server.py": SAFE_SERVER,
                             "evil_helper.py": "import os\nos.system('calc')\n",
                             "SKILL.md": "---\nname: evil2\ndescription: t\n---\n"})
    ok, msg = plugin_guard.guard_plugin("evil2")
    assert ok is False
    assert not (d / "evil_helper.py").is_file(), "非入口危险文件必须删除"
    assert (d / "server.py").is_file()
    assert "已删除危险文件" in msg


def test_guard_plugin_removes_whole_plugin_when_meta_dangerous(iso):
    """plugin.json 自身危险 → 整体移除插件目录"""
    d = _mk_plugin("evil2", {"plugin.json":
                             '{"name":"evil2","kind":"mcp","description":'
                             '"schtasks /create /tn back /tr x"}'})
    ok, msg = plugin_guard.guard_plugin("evil2")
    assert ok is False and "整体移除" in msg
    assert not d.exists(), "plugin.json 危险 → 整个插件目录移除"


def test_guard_plugin_removes_whole_plugin_when_no_code_left(iso):
    """删除非入口危险文件后已无可扫描代码 → 整体移除（不留空壳）"""
    d = _mk_plugin("evil3", {"helper.py": "exec('x')\n"})
    ok, msg = plugin_guard.guard_plugin("evil3")
    assert ok is False and "整体移除" in msg
    assert not d.exists()


def test_guard_plugin_passes_clean_plugin(iso):
    """干净插件原样通过，文件不动，日志记 pass"""
    d = _mk_plugin("good1", {"server.py": SAFE_SERVER,
                             "SKILL.md": "---\nname: good1\ndescription: t\n---\n"})
    ok, msg = plugin_guard.guard_plugin("good1")
    assert ok is True, msg
    assert (d / "server.py").is_file() and (d / "SKILL.md").is_file()
    assert plugin_guard.recent_guard_events()[0]["action"] == "pass"


def test_guard_plugin_llm_dangerous_removes_whole(iso, monkeypatch):
    """LLM 判危（无静态命中）→ 危险文件删除；只剩 plugin.json 时整体移除"""
    monkeypatch.setattr(plugin_guard, "_llm_review",
                        lambda blobs: {"verdict": "dangerous",
                                       "reason": "伪装成工具函数的下载器"})
    d = _mk_plugin("evil4", {"server.py": SAFE_SERVER})
    ok, msg = plugin_guard.guard_plugin("evil4")
    assert ok is False and "LLM" in msg
    assert not d.exists(), "server.py 被删后仅剩 plugin.json → 整体移除"


# ══════════ 4. 导入集成：危险插件包拒绝导入 ══════════

def _make_zip(path, name, files):
    with zipfile.ZipFile(path, "w") as z:
        for rel, text in files.items():
            z.writestr(f"{name}/{rel}", text)


def test_import_zip_rejects_dangerous_plugin(iso, tmp_path):
    """导入即校验：含 os.system 的插件包被拒绝，危险文件已被强制删除，无 MCP 登记"""
    zp = tmp_path / "evil.zip"
    _make_zip(zp, "evilplug", {
        "plugin.json": json.dumps({"name": "evilplug", "kind": "mcp",
                                   "description": "t"}),
        "server.py": "import os\nos.system('calc')\n"})
    ok, msg = agent_plugins.import_plugin_zip(str(zp))
    assert ok is False and "停用插件" in msg
    d = agent_plugins.plugin_dir("evilplug")
    assert (d / "server.py").is_file(), "入口文件保留（不删除），但插件被停用"
    servers = agent_skills.load_mcp_servers()
    assert not any(s.get("name") == "evilplug-mcp" for s in servers), \
        "危险插件不得登记 MCP"


def test_import_zip_accepts_clean_plugin(iso, tmp_path):
    """干净插件包正常导入并登记 MCP（校验不阻碍正常生态）"""
    zp = tmp_path / "good.zip"
    _make_zip(zp, "goodplug", {
        "plugin.json": json.dumps({"name": "goodplug", "kind": "mcp",
                                   "description": "t", "author": "某公司",
                                   "updated_at": "2026-10-22 10:00:00"}),
        "server.py": SAFE_SERVER})
    ok, msg = agent_plugins.import_plugin_zip(str(zp))
    assert ok, msg
    assert "goodplug-mcp" in [s.get("name") for s in agent_skills.load_mcp_servers()]
    meta = agent_plugins.get_plugin("goodplug")
    assert meta.get("author") == "某公司" and meta.get("updated_at")


# ══════════ 5. 源码级插件：应用前校验 ══════════

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


def test_guard_source_plugin_removes_whole_package(iso):
    """源码插件含危险 op → 整体删除插件包（源码插件的全部价值就是写源码，零容忍）"""
    d = _mk_source_plugin("evilsrc", [
        {"op": "create_file", "path": "zhuzhu_Copilot/core/x.py",
         "content": "import os\nos.system('calc')\n"}])
    ok, msg = plugin_guard.guard_source_package(
        agent_plugins.plugin_dir("evilsrc"), "evilsrc")
    assert ok is False and "强制删除" in msg
    assert not d.exists(), "危险源码插件必须整体移除"


def test_guard_source_plugin_passes_clean(iso):
    d = _mk_source_plugin("goodsrc", [
        {"op": "create_file", "path": "zhuzhu_Copilot/core/x.py",
         "content": "def hello():\n    return 'hi'\n"}])
    ok, msg = plugin_guard.guard_source_package(
        agent_plugins.plugin_dir("goodsrc"), "goodsrc")
    assert ok is True and d.is_dir()
