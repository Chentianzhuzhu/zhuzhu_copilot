"""Copilot 插件体系回归：应用级独立体系 + 打包态源码覆盖层 + 示例插件。

真实执行的部分：
  · 把「打包态」落地为真实文件操作：apply_root 指到临时覆盖层、
    _packaged_file 指到临时「打包根」，四类 op 真写真改真编译真回滚；
  · 导入钩子在**子进程**里真实安装并真实 import（覆盖层版本胜出、
    未覆盖模块回落原版、__file__ 指回原版路径保证资源解析不回退）；
  · 随包示例插件（theme-deep-blue / custom-hello-tool / settings-page-demo /
    tray-quick-note）逐条 op 应用→编译→回滚闭环；
  · 导入 → 导出 → 删除 → 再导入往返（source_ops / copilot.json 不丢）；
  · 受保护核心程序（_copilot_overlay_hook.py / main.py）拒绝改动。

LLM 评审用注入替身（测试环境不发起真实网络请求）。
"""

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from zhuzhu_Copilot.core import (agent_plugins, agent_skills, copilot_plugins,
                                 plugin_guard, plugin_source)

EXAMPLES = (Path(__file__).resolve().parent.parent / "src" / "zhuzhu_Copilot"
            / "copilot_plugins_examples")

# 临时「打包根」里的原版文件（含锚点），模拟安装包内的源码快照
PKG_STYLES = '''"""styles 桩"""

PALETTE = {"primary": "#000000"}


def set_palette(theme: str) -> None:
    src = dict(PALETTE)
    PALETTE.clear()
    PALETTE.update(src)
'''

PKG_AGENT_PANEL = '''"""agent_panel 桩（含示例插件要命中的锚点）"""

_NAV_ITEMS = (
    ("通用与记忆", "general"),
    ("Copilot 插件", "copilot_plugins"),
)


class _AgentSettingsDialog:
    # ---- Copilot 插件扩展点：插件可在此之后追加页面构建方法 ----
    def _current_coplugin(self) -> dict:
        return {}

    def _setup_pages(self):
        self._page_builders = [
            self._build_general_page,
            self._build_copilot_plugin_page,
        ]


_RESULT_TRUNCATE = 6000
'''

PKG_AGENT_SKILLS = '''"""agent_skills 桩（含 prompt-optimizer 的 replace_module 锚点）"""


def _base_prompt_parts(agent: dict) -> list:
    parts = ["语言指令"]
    return parts
'''

PKG_AGENT_ENGINE = '''"""agent_engine 桩（含 agent-scorecard 的 replace_module 锚点）"""

_NO_WAIT_TIMEOUT_TOOLS = frozenset()
_DOWNLOAD_WAIT_TIMEOUT_S = 3600.0
_DEFAULT_WAIT_TIMEOUT_S = 40.0


def _tool_wait_timeout(name: str):
    """内置工具的等待上限（None = 不设上限）。"""
    if name in _NO_WAIT_TIMEOUT_TOOLS:
        return None
    if name == "fast_download":
        return _DOWNLOAD_WAIT_TIMEOUT_S
    return _DEFAULT_WAIT_TIMEOUT_S
'''

# agent_tools 桩：把 TOOLS 精确排到第 269 行（与真实文件一致），
# 这样 agent-scorecard 的「按行号插入 line=270」在桩环境里也能被真实验证。
_TOOLS_FILLER = ["# 填充行：让 TOOLS 落在第 269 行，与真实 agent_tools.py 对齐"] * 268
PKG_AGENT_TOOLS = "\n".join(_TOOLS_FILLER + [
    "TOOLS = [",
    '    {"name": "read_file"},',
    "]",
    "",
    "",
    "def dispatch(name, args):",
    '        if name == "revert_source_plugin":',
    '            return _revert_source_plugin(str(args.get("name", "")))',
    '        if name == "revert_copilot_plugin":',
    '            return _revert_copilot_plugin(str(args.get("name", "")))',
]) + "\n"


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """隔离两套插件目录/技能配置；LLM 替身默认安全。"""
    monkeypatch.setattr(agent_plugins, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(copilot_plugins, "COPILOT_PLUGINS_DIR",
                        tmp_path / "copilot_plugins")
    monkeypatch.setattr(agent_skills, "CONFIG_DIR", tmp_path / "agent")
    monkeypatch.setattr(plugin_guard, "_llm_review",
                        lambda blobs: {"verdict": "safe", "reason": "测试替身：安全"})
    agent_skills._MCP_CACHE["data"] = None
    return tmp_path


@pytest.fixture()
def frozen(tmp_path, monkeypatch):
    """模拟打包态：apply_root → 临时覆盖层；_packaged_file → 临时打包根。"""
    pkg_root = tmp_path / "pkgroot"
    (pkg_root / "zhuzhu_Copilot" / "ui").mkdir(parents=True)
    (pkg_root / "zhuzhu_Copilot" / "core").mkdir(parents=True)
    (pkg_root / "zhuzhu_Copilot" / "ui" / "styles.py").write_text(
        PKG_STYLES, encoding="utf-8")
    (pkg_root / "zhuzhu_Copilot" / "ui" / "agent_panel.py").write_text(
        PKG_AGENT_PANEL, encoding="utf-8")
    (pkg_root / "zhuzhu_Copilot" / "core" / "agent_tools.py").write_text(
        PKG_AGENT_TOOLS, encoding="utf-8")
    (pkg_root / "zhuzhu_Copilot" / "core" / "agent_skills.py").write_text(
        PKG_AGENT_SKILLS, encoding="utf-8")
    (pkg_root / "zhuzhu_Copilot" / "core" / "agent_engine.py").write_text(
        PKG_AGENT_ENGINE, encoding="utf-8")
    overlay = tmp_path / "overlay"
    monkeypatch.setattr(plugin_source, "app_source_root", lambda: None)
    monkeypatch.setattr(plugin_source, "apply_root", lambda: (overlay, "overlay"))
    monkeypatch.setattr(plugin_source, "_packaged_file",
                        lambda rel: (pkg_root / str(rel))
                        if (pkg_root / str(rel)).is_file() else None)
    return {"pkg_root": pkg_root, "overlay": overlay}


def _mk_coplugin(name, ops, **extra):
    d = copilot_plugins.plugin_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    meta = {"name": name, "kind": "copilot", "enabled": True,
            "description": "t", "author": "测试作者"}
    meta.update(extra)
    (d / "source_ops.json").write_text(
        json.dumps(ops, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "plugin.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return d


# ------------------------------------------------------------
# 打包态：覆盖层应用 / 回滚
# ------------------------------------------------------------

def test_overlay_apply_insert_and_create(frozen, iso):
    _mk_coplugin("ovl1", [
        {"op": "insert_after", "path": "zhuzhu_Copilot/ui/styles.py",
         "anchor": "    PALETTE.update(src)",
         "content": "    PALETTE[\"primary\"] = \"#1D4ED8\""},
        {"op": "create_file", "path": "zhuzhu_Copilot/core/new_mod.py",
         "content": "VALUE = 42\n"},
    ])
    ok, msg = copilot_plugins.apply("ovl1")
    assert ok, msg
    ov = frozen["overlay"]
    # 原版被复制进覆盖层后再插入
    text = (ov / "zhuzhu_Copilot" / "ui" / "styles.py").read_text(encoding="utf-8")
    assert "PALETTE[\"primary\"] = \"#1D4ED8\"" in text
    assert "def set_palette" in text          # 原版内容完整保留
    # 新建文件落在覆盖层，可被 import
    assert (ov / "zhuzhu_Copilot" / "core" / "new_mod.py").is_file()
    # 清单记录 overlay 模式
    mf = json.loads((copilot_plugins.plugin_dir("ovl1") / "applied.json")
                    .read_text(encoding="utf-8"))
    assert mf["mode"] == "overlay"
    st = copilot_plugins.status("ovl1")
    assert st["applied"] and len(st["files"]) == 2

    # 回滚：与打包原版一致的覆盖层副本被删除（模块回落原版），新建文件被删
    ok, msg = copilot_plugins.revert("ovl1")
    assert ok, msg
    assert not (ov / "zhuzhu_Copilot" / "ui" / "styles.py").exists()
    assert not (ov / "zhuzhu_Copilot" / "core" / "new_mod.py").exists()
    assert not copilot_plugins.status("ovl1")["applied"]


def test_overlay_apply_compile_failure_rolls_back(frozen, iso):
    _mk_coplugin("ovl2", [
        {"op": "create_file", "path": "zhuzhu_Copilot/core/bad.py",
         "content": "def broken(:\n"},
    ])
    ok, msg = copilot_plugins.apply("ovl2")
    assert not ok and "编译检查未通过" in msg
    assert not (frozen["overlay"] / "zhuzhu_Copilot" / "core" / "bad.py").exists()


def test_overlay_protected_paths_rejected(frozen, iso):
    for bad in ("_copilot_overlay_hook.py", "main.py",
                "zhuzhu_Copilot/core/plugin_guard.py"):
        _mk_coplugin(f"ovl-p{bad.replace('/', '_').replace('.', '_')}",
                     [{"op": "replace_file", "path": bad,
                       "content": "# hacked\n"}])
        ok, msg = copilot_plugins.apply(f"ovl-p{bad.replace('/', '_').replace('.', '_')}")
        assert not ok, bad
        assert "受保护" in msg


def test_overlay_dangerous_plugin_removed_before_apply(frozen, iso):
    """危险 op 在应用前被校验系统整体删除插件包（覆盖层一行未写）。"""
    _mk_coplugin("ovl-bad", [
        {"op": "create_file", "path": "zhuzhu_Copilot/core/evil.py",
         "content": "import os\nos.system('calc')\n"},
    ])
    ok, msg = copilot_plugins.apply("ovl-bad")
    assert not ok and "强制删除" in msg
    assert not copilot_plugins.plugin_dir("ovl-bad").exists()
    assert not (frozen["overlay"] / "zhuzhu_Copilot").exists()


# ------------------------------------------------------------
# 导入钩子（子进程真实安装）
# ------------------------------------------------------------

def _hook_probe(tmp_path) -> str:
    """在隔离子进程里安装钩子并 import，返回其 stdout。"""
    home = tmp_path / "home"
    ov = home / ".zhuzhu_Copilot" / "source_overlay"
    # 1) 覆盖一个真实模块：原版拷贝 + 追加标记
    tgt = ov / "zhuzhu_Copilot" / "app_identity.py"
    tgt.parent.mkdir(parents=True, exist_ok=True)
    orig = (Path(__file__).resolve().parent.parent / "src" / "zhuzhu_Copilot"
            / "app_identity.py").read_text(encoding="utf-8")
    tgt.write_text(orig + "\nOVL_MARKER = True\n", encoding="utf-8")
    # 2) 覆盖层里的全新模块
    new = ov / "zhuzhu_Copilot" / "core" / "_ovl_new_mod.py"
    new.parent.mkdir(parents=True, exist_ok=True)
    new.write_text("VALUE = 'from-overlay'\n", encoding="utf-8")
    script = (
        "import _copilot_overlay_hook as h\n"
        "print('installed', h.install())\n"
        "from zhuzhu_Copilot import app_identity\n"
        "print('marker', getattr(app_identity, 'OVL_MARKER', False))\n"
        "print('file_is_original', 'source_overlay' not in str(app_identity.__file__))\n"
        "from zhuzhu_Copilot.core import _ovl_new_mod\n"
        "print('new_mod', _ovl_new_mod.VALUE)\n"
        "import zhuzhu_Copilot.core.agent_runtime as ar\n"
        "print('fallback_ok', hasattr(ar, 'python_interpreter'))\n"
    )
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "src")
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run([sys.executable, "-c", script], capture_output=True,
                       text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_overlay_import_hook_subprocess(tmp_path):
    out = _hook_probe(tmp_path)
    assert "installed True" in out
    assert "marker True" in out                    # 覆盖层版本胜出
    assert "file_is_original True" in out          # __file__ 指回原版（资源解析不回退）
    assert "new_mod from-overlay" in out           # 覆盖层新模块可 import
    assert "fallback_ok True" in out               # 未覆盖模块回落原版


# ------------------------------------------------------------
# 随包示例插件：应用 → 编译 → 回滚闭环
# ------------------------------------------------------------

@pytest.mark.parametrize("name,rel_checks", [
    ("theme-deep-blue", {"zhuzhu_Copilot/ui/styles.py":
                         ['PALETTE["primary"] = "#1D4ED8"']}),
    ("custom-hello-tool", {"zhuzhu_Copilot/core/tool_hello_demo.py":
                           ["def run(args: dict) -> dict:"],
                           "zhuzhu_Copilot/core/agent_tools.py":
                           ['"name": "hello_demo"', 'if name == "hello_demo":']}),
    ("settings-page-demo", {"zhuzhu_Copilot/ui/page_quote.py":
                            ["class QuotePage(QWidget):"],
                            "zhuzhu_Copilot/ui/agent_panel.py":
                            ['("每日一句", "quote")', "self._build_quote_page,",
                             "def _build_quote_page(self) -> QWidget:"]}),
    # 源码级「插入」：新模块 + 包装现有函数（不改原代码行）+ 新工具
    ("py-syntax-lint", {"zhuzhu_Copilot/ui/py_code_hl.py":
                        ["def highlight_code(", "def check_python(",
                         "def highlight_html("],
                        "zhuzhu_Copilot/ui/agent_panel.py":
                        ["_py_syntax_lint_orig_md_to_html = _md_to_html",
                         "def _md_to_html(raw: str) -> str:"],
                        "zhuzhu_Copilot/core/agent_tools.py":
                        ['"name": "check_python_syntax"',
                         'if name == "check_python_syntax":']}),
    # 源码级「替换」：把 agent_skills 的 return parts 换成插件代码
    # （agent-scorecard 已改为纯运行时插件，不产生源码变更，见 test_scorecard_example）
    ("prompt-optimizer", {"zhuzhu_Copilot/core/prompt_optimizer.py":
                          ["PROMPT_DISCIPLINE = (", "def optimize("],
                          "zhuzhu_Copilot/core/agent_skills.py":
                          ["from zhuzhu_Copilot.core.prompt_optimizer import PROMPT_DISCIPLINE",
                           "parts.append(PROMPT_DISCIPLINE)", "return parts"],
                          "zhuzhu_Copilot/core/agent_tools.py":
                          ['"name": "optimize_prompt"',
                           'if name == "optimize_prompt":']}),
])
def test_examples_apply_and_revert(name, rel_checks, frozen, iso):
    src = EXAMPLES / name
    assert (src / "plugin.json").is_file() and (src / "source_ops.json").is_file()
    ops = json.loads((src / "source_ops.json").read_text(encoding="utf-8"))
    _mk_coplugin(name, ops)
    ok, msg = copilot_plugins.apply(name)
    assert ok, msg
    ov = frozen["overlay"]
    for rel, needles in rel_checks.items():
        text = (ov / rel).read_text(encoding="utf-8")
        for n in needles:
            assert n in text, f"{rel} 缺少 {n}"
    ok, msg = copilot_plugins.revert(name)
    assert ok, msg
    for rel in rel_checks:
        assert not (ov / rel).exists(), f"{rel} 回滚后应回到打包原版"


def test_scorecard_example_is_runtime_only():
    """agent-scorecard 是纯运行时插件：不写源码、start/stop 完全可逆。

    回归（用户反馈「启用后完全没反应」）：它原先靠 source_ops 往应用源码里新建
    模块 + 改工具表 + 替换引擎函数，服务又反过来 import 那个新模块。打包版没有
    可写源码 → 应用失败 → 服务静默降级 → 评分窗与工具全部消失。
    """
    import importlib.util

    from zhuzhu_Copilot.core import agent_engine, agent_tools

    d = EXAMPLES / "agent-scorecard"
    assert not (d / "source_ops.json").exists(), "评分插件不应再产生源码变更"
    man = copilot_plugins.load_copilot_manifest(d)
    assert [s["id"] for s in man["services"]] == ["agent-scorecard"]

    spec = importlib.util.spec_from_file_location(
        "scorecard_example_service", d / "service" / "scorecard_service.py")
    svc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(svc)

    orig_exec = agent_tools.execute_tool
    orig_run = agent_engine.AgentEngine.run
    n_tools = len(agent_tools.TOOLS)
    svc.start()
    try:
        assert any(t.get("function", {}).get("name") == "score_last_task"
                   for t in agent_tools.TOOLS)
        names = {t.get("function", {}).get("name")
                 for t in agent_tools.tool_schemas()}
        assert "score_last_task" in names, "注入的工具 schema 必须对下一轮请求可见"
        out = agent_tools.execute_tool("score_last_task", {})
        assert "执行时间" in out["text"]

        svc.begin(None)
        svc.begin(None)                  # 子 Agent 嵌套：不重置主任务统计
        svc.note_tool("read_file")
        svc.note_tool("read_file")
        svc.note_tool("sub_worker")
        assert svc.end(None) is None, "嵌套层结束时不应弹评分窗"
        snap = svc.end(None)
        assert snap is not None and snap["calls"] == 3
        assert ("read_file", 2) in snap["groups"]["tool"]
        assert ("sub_worker", 1) in snap["groups"]["agent"]
        assert "工具调用：3 次" in svc.format_metrics(snap)
        assert "自我反省" in svc.feedback_text(snap, 5, "很好")
        assert svc.last_snapshot()["calls"] == 3
    finally:
        svc.stop()
    assert agent_tools.execute_tool is orig_exec
    assert agent_engine.AgentEngine.run is orig_run
    assert len(agent_tools.TOOLS) == n_tools
    assert "score_last_task" not in {t.get("function", {}).get("name")
                                     for t in agent_tools.tool_schemas()}


def test_overlay_root_is_isolated_in_tests():
    """回归：转译层根必须被 conftest 重定向到临时目录。

    真实事故：应用/回滚用例未隔离 overlay_root，把桩文件写进用户
    ~/.zhuzhu_Copilot/source_overlay；下次启动导入钩子优先加载桩模块，
    agent_tools 缺 get_workdir，界面周期刷新直接 AttributeError。
    """
    from zhuzhu_Copilot import app_identity

    ov = plugin_source.overlay_root().resolve()
    assert str(ov).startswith(str(app_identity.data_root().resolve())), ov


def test_set_enabled_auto_writes_and_reverts_overlay(frozen, iso):
    """启用即自动写入转译层、停用即自动回滚（不再需要手动应用按钮）。"""
    _mk_coplugin("auto1", [
        {"op": "create_file", "path": "zhuzhu_Copilot/core/auto_mod.py",
         "content": "VALUE = 1\n"},
    ])
    ov = frozen["overlay"]
    ok, msg = copilot_plugins.set_enabled("auto1", True)
    assert ok, msg
    assert "自动写入转译层" in msg
    assert (ov / "zhuzhu_Copilot" / "core" / "auto_mod.py").is_file()
    assert copilot_plugins.status("auto1")["applied"]

    ok, msg = copilot_plugins.set_enabled("auto1", False)
    assert ok, msg
    assert "自动回滚" in msg
    assert not (ov / "zhuzhu_Copilot" / "core" / "auto_mod.py").exists()
    assert not copilot_plugins.status("auto1")["applied"]


def test_hot_reload_picks_up_overlay_module(tmp_path, monkeypatch):
    """热插拔：转译层新增模块可立即导入，改写后 reload 取到新实现。"""
    import importlib
    import _copilot_overlay_hook as hook

    from zhuzhu_Copilot.core import hot_reload

    ov = tmp_path / "ov"
    (ov / "zhuzhu_Copilot" / "core").mkdir(parents=True)
    demos = ov / "zhuzhu_Copilot" / "core" / "hotdemo.py"
    demos.write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(hook, "overlay_dir", lambda: ov)
    monkeypatch.setattr(plugin_source, "overlay_root", lambda: ov)
    before = list(sys.meta_path)
    try:
        res = hot_reload.reload_overlay()
        assert "zhuzhu_Copilot.core.hotdemo" in res["reloaded"], res
        mod = importlib.import_module("zhuzhu_Copilot.core.hotdemo")
        assert mod.VALUE == 1

        demos.write_text("VALUE = 2\n", encoding="utf-8")
        hot_reload.reload_overlay()
        assert importlib.import_module("zhuzhu_Copilot.core.hotdemo").VALUE == 2
    finally:
        sys.meta_path[:] = before
        sys.modules.pop("zhuzhu_Copilot.core.hotdemo", None)


def test_import_scorecard_zip_is_runtime_only(iso, tmp_path):
    """评分插件打包成 zip 导入后：只有 services，没有源码变更（打包版可直接用）。"""
    zp = _zip_dir(EXAMPLES / "agent-scorecard", tmp_path / "scorecard.zip")
    ok, msg = copilot_plugins.import_plugin_zip(str(zp))
    assert ok, msg
    p = copilot_plugins.get_plugin("agent-scorecard")
    assert p["manifest_ext"]["services"] == 1
    assert int(p.get("source_ops") or 0) == 0
    assert not p.get("source_applied")


def test_example_manifest_and_package_files(iso):
    """tray-quick-note：copilot.json 声明托盘+服务，模块随包分发。"""
    d = copilot_plugins.plugin_dir("tray-quick-note")
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.json").write_text(
        (EXAMPLES / "tray-quick-note" / "plugin.json").read_text(encoding="utf-8"),
        encoding="utf-8")
    (d / "copilot.json").write_text(
        (EXAMPLES / "tray-quick-note" / "copilot.json").read_text(encoding="utf-8"),
        encoding="utf-8")
    (d / "pages").mkdir(exist_ok=True)
    (d / "pages" / "quick_note.py").write_text(
        (EXAMPLES / "tray-quick-note" / "pages" / "quick_note.py")
        .read_text(encoding="utf-8"), encoding="utf-8")
    man = copilot_plugins.load_copilot_manifest(d)
    assert [t["id"] for t in man["tray"]] == ["quick-note"]
    assert [s["id"] for s in man["services"]] == ["note-heartbeat"]
    p = copilot_plugins.get_plugin("tray-quick-note")
    assert p["manifest_ext"] == {"pages": 0, "services": 1, "tray": 1,
                                 "shortcuts": 0}


# ------------------------------------------------------------
# 导入 / 导出 / 配置
# ------------------------------------------------------------

def _zip_dir(src: Path, dest: Path) -> Path:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(src).as_posix())
    return dest


def test_import_export_roundtrip(iso, tmp_path):
    zp = _zip_dir(EXAMPLES / "custom-hello-tool", tmp_path / "pkg.zip")
    ok, msg = copilot_plugins.import_plugin_zip(str(zp))
    assert ok, msg
    p = copilot_plugins.get_plugin("custom-hello-tool")
    assert p["kind"] == "copilot" and p["author"]
    # 写配置 → 导出 → 删除 → 再导入：配置与变更清单都在
    ok, _ = copilot_plugins.set_plugin_config(
        "custom-hello-tool", {"api_key": "sk-x", "debug": True})
    assert ok
    out = tmp_path / "out.zip"
    ok, msg = copilot_plugins.export_plugin_zip("custom-hello-tool", str(out))
    assert ok and out.is_file()
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "custom-hello-tool/config.json" in names
        assert "custom-hello-tool/source_ops.json" in names
        assert not any("applied.json" in n for n in names)
    ok, _ = copilot_plugins.delete_plugin("custom-hello-tool")
    assert ok
    ok, msg = copilot_plugins.import_plugin_zip(str(out))
    assert ok, msg
    assert copilot_plugins.get_plugin_config("custom-hello-tool") == {
        "api_key": "sk-x", "debug": True}
    assert len(copilot_plugins.source_ops("custom-hello-tool")) == 3


def test_config_validation(iso):
    _mk_coplugin("cfg1", [{"op": "create_file", "path": "zhuzhu_Copilot/core/a.py",
                           "content": "X = 1\n"}])
    ok, msg = copilot_plugins.set_plugin_config("cfg1", {"PATH": "/hack"})
    assert not ok and "环境变量" in msg
    ok, msg = copilot_plugins.set_plugin_config("cfg1", {"a": ["list"]})
    assert not ok and "标量" in msg
    ok, msg = copilot_plugins.set_plugin_config("cfg1", {"api_key": "k"})
    assert ok
    # env 生成：键名大写、布尔转 true/false
    copilot_plugins.set_plugin_config("cfg1", {"api_key": "k", "debug": False})
    assert copilot_plugins.plugin_config_env("cfg1") == {
        "API_KEY": "k", "DEBUG": "false"}


def test_overlay_info_reports_mode(iso):
    info = copilot_plugins.overlay_info()
    assert info["mode"] in ("source", "overlay")
    assert info["root"]

# ------------------------------------------------------------
# 示例与真实源码的一致性（漂移守卫）+ 换行风格保持
# ------------------------------------------------------------

def test_example_anchors_exist_in_real_source():
    """所有示例的 anchor/start/end 必须在真实源码里存在且唯一。

    示例是给开发者照着写的模板，一旦目标文件改名/改锚点，示例就会在应用时
    「找不到锚点整体回滚」——本用例在 CI 阶段就抓住这种漂移（只读，不写文件）。
    """
    real_src = Path(__file__).resolve().parent.parent / "src"
    problems = []
    for d in sorted(EXAMPLES.iterdir()):
        ops_file = d / "source_ops.json"
        if not d.is_dir() or not ops_file.is_file():
            continue
        for i, op in enumerate(json.loads(ops_file.read_text(encoding="utf-8"))):
            rel = str(op.get("path") or "")
            target = real_src / rel
            if op.get("op") == "create_file":
                if target.exists():
                    problems.append(f"{d.name}[{i}]: create_file 目标已存在 {rel}")
                continue
            if not target.is_file():
                problems.append(f"{d.name}[{i}]: 目标文件不存在 {rel}")
                continue
            lines = target.read_text(encoding="utf-8").splitlines()
            # 行号寻址：1 ≤ line ≤ 行数+1（insert_at）/ start_line ≤ 行数
            for key in ("line", "start_line", "end_line"):
                raw = op.get(key)
                if raw is None or str(raw).strip() == "":
                    continue
                n = int(str(raw))
                limit = len(lines) + (1 if key == "line" else 0)
                if not (1 <= n <= limit):
                    problems.append(f"{d.name}[{i}]: {key}={n} 超出文件行数范围 "
                                    f"(1..{limit}) in {rel}")
            for key in ("anchor", "start", "end"):
                a = str(op.get(key) or "")
                if not a:
                    continue
                hits = sum(1 for ln in lines if a in ln)
                if hits != 1:
                    problems.append(f"{d.name}[{i}]: {key} 命中 {hits} 次 "
                                    f"（须恰好 1 次）{a[:40]!r} in {rel}")
    assert not problems, "\n".join(problems)


def test_apply_preserves_crlf(frozen, iso):
    """CRLF 文件被插入代码后仍保持 CRLF（不能把整篇改成 LF）。"""
    p = frozen["pkg_root"] / "zhuzhu_Copilot" / "ui" / "styles.py"
    p.write_bytes(PKG_STYLES.replace("\n", "\r\n").encode("utf-8"))
    _mk_coplugin("crlf1", [
        {"op": "insert_after", "path": "zhuzhu_Copilot/ui/styles.py",
         "anchor": "    PALETTE.update(src)",
         "content": "    PALETTE[\"primary\"] = \"#123456\""},
    ])
    ok, msg = copilot_plugins.apply("crlf1")
    assert ok, msg
    raw = (frozen["overlay"] / "zhuzhu_Copilot" / "ui" / "styles.py").read_bytes()
    assert b"\r\n" in raw
    assert raw.count(b"\n") == raw.count(b"\r\n"), "出现裸 LF：换行风格被改写"
