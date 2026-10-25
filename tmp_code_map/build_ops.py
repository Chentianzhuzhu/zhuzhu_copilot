"""生成并校验 code-map 插件的 source_ops.json（构建期工具，不随插件分发）。

与 perf-turbo 的构建脚本同一套路：
  1. 读取 source/code_map.py，生成 source_ops.json；
  2. 在 src 的临时副本上真实执行每条 op（走应用自己的源码变更引擎），再 py_compile；
  3. 静态确认改动落位正确（schema / 分发接线都在）；
  4. 用插件校验系统的静态规则扫描所有待写入内容（danger 必须 0 命中）；
  5. 在独立子进程里跑内核自测（造一棵已知结构的临时目录，验证统计口径）。
"""

import json
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
PKG = SRC / "zhuzhu_Copilot" / "copilot_plugins_examples" / "code-map"
KERNEL = PKG / "source" / "code_map.py"
TOOLS = "zhuzhu_Copilot/core/agent_tools.py"

SCHEMA = '''    # ---- Copilot 插件 code-map：工作区代码地图工具 ----
    {
        "type": "function",
        "function": {
            "name": "code_map",
            "description": "扫描一个目录并统计规模与结构：文件数、目录数、代码行数、总体积、"
                           "按扩展名分布、按一级子目录分布、行数最多的文件。适合在动手改代码前"
                           "先建立全局认知（「这个项目多大」「代码主要在哪」「哪些文件最重」），"
                           "或判断某目录是否值得全量读取。只读扫描，不改任何文件；"
                           "自动跳过 .git/node_modules/dist/build/__pycache__ 等依赖与产物目录，"
                           "符号链接不跟随；超过 2 万文件或 20 秒会自动截断并标注。",
            "parameters": {"type": "object",
                           "properties": {
                               "path": {"type": "string",
                                        "description": "要扫描的目录绝对路径；不传则用上次扫描过的目录"},
                               "depth": {"type": "integer",
                                         "description": "目录深度上限 1~6（默认 4）"},
                               "top": {"type": "integer",
                                       "description": "每节列出多少条 3~30（默认 8）"}},
                           "required": []},
        },
    },'''

DISPATCH = '''        # ---- Copilot 插件 code-map：代码地图工具接线 ----
        if name == "code_map":
            from zhuzhu_Copilot.core import code_map as _cm
            return _cm.tool_report(args)'''


def build_ops() -> list:
    return [
        {"op": "create_file",
         "path": "zhuzhu_Copilot/core/code_map.py",
         "content": KERNEL.read_text(encoding="utf-8")},
        {"op": "insert_after", "path": TOOLS, "anchor": "TOOLS = [",
         "content": SCHEMA},
        {"op": "insert_after", "path": TOOLS,
         "anchor": '            return _revert_copilot_plugin(str(args.get("name", "")))',
         "content": DISPATCH},
    ]


def write_ops(ops: list) -> None:
    (PKG / "source_ops.json").write_text(
        json.dumps(ops, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[1] source_ops.json：%d 条 op / %d 个目标文件"
          % (len(ops), len({o["path"] for o in ops})))


def apply_to_temp(ops: list) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="cmops_"))
    root = tmp / "src"
    for rel in {o["path"] for o in ops if o["op"] != "create_file"}:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC / rel, dst)
    ok_all = True
    sys.path.insert(0, str(SRC))
    try:
        from zhuzhu_Copilot.core import plugin_source
        for i, op in enumerate(ops):
            try:
                rec = plugin_source._apply_op(root, op)
                print("    op%-2d %-14s %s" % (i + 1, op["op"], rec["path"]))
            except Exception as e:
                print("    op%-2d 失败：%s" % (i + 1, e))
                ok_all = False
        for rel in sorted({o["path"] for o in ops}):
            try:
                py_compile.compile(str(root / rel), doraise=True)
            except py_compile.PyCompileError as e:
                print("    编译失败 %s：%s" % (rel, str(e)[:300]))
                ok_all = False
    finally:
        sys.path.remove(str(SRC))
    print("[2] 临时副本执行 + 编译：%s" % ("通过" if ok_all else "失败"))
    if not ok_all:
        raise SystemExit("临时副本校验失败")
    tools = (root / TOOLS).read_text(encoding="utf-8")
    checks = [("schema 已插入", '"name": "code_map"' in tools),
              ("分发已接线", 'if name == "code_map":' in tools),
              ("原分发锚点仍在", 'return _revert_copilot_plugin(' in tools)]
    bad = [n for n, ok in checks if not ok]
    print("[3] 落位检查：%s" % ("通过" if not bad else "缺失 " + "、".join(bad)))
    if bad:
        raise SystemExit(1)
    return root


def guard_scan(ops: list) -> None:
    sys.path.insert(0, str(SRC))
    try:
        from zhuzhu_Copilot.core import plugin_guard
        hits = []
        for i, op in enumerate(ops):
            for f in plugin_guard.static_scan(op["content"], str(op["path"])):
                hits.append((i + 1, f))
        for rel in ("service/cm_service.py", "pages/code_map_page.py"):
            text = (PKG / rel).read_text(encoding="utf-8")
            for f in plugin_guard.static_scan(text, rel):
                hits.append((rel, f))
    finally:
        sys.path.remove(str(SRC))
    print("[4] 静态规则扫描：%d 处命中" % len(hits))
    for where, f in hits:
        print("    %s → %s（%s）第 %s 行：%s"
              % (where, f["rule"], f["severity"], f["line"], f["snippet"][:70]))
    if any(f["severity"] == "danger" for _w, f in hits):
        raise SystemExit("存在 danger 命中，插件会被校验系统删除")


_SELFTEST = r'''
import json, sys, tempfile
from pathlib import Path
from zhuzhu_Copilot.core import code_map as k

root = Path(tempfile.mkdtemp(prefix="cm_tree_"))
(root / "src").mkdir()
(root / "src" / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
(root / "src" / "b.ts").write_text("let a = 1;\n", encoding="utf-8")
(root / "docs").mkdir()
(root / "docs" / "readme.md").write_text("# t\n\nbody\n", encoding="utf-8")
(root / "node_modules").mkdir()
(root / "node_modules" / "junk.js").write_text("a\nb\nc\n", encoding="utf-8")
(root / "bin.dat").write_bytes(b"\x00\x01\x02")
(root / "empty.py").write_text("", encoding="utf-8")

d = k.scan(root)
assert d["root"] == str(root.resolve())
assert d["files"] == 5, d["files"]                       # a.py b.ts readme.md bin.dat empty.py（node_modules 整目录跳过）
assert d["dirs"] == 3, d["dirs"]                        # src docs node_modules
assert d["code_files"] == 4, d["code_files"]            # a.py b.ts readme.md empty.py（junk.js 在排除目录）
assert d["code_lines"] == 2 + 1 + 3 + 0, d["code_lines"]
exts = {r["ext"]: r["lines"] for r in d["by_ext"]}
assert exts[".py"] == 2 and exts[".ts"] == 1 and exts[".md"] == 3, exts
dirs = {r["dir"]: r["files"] for r in d["by_dir"]}
assert dirs["src"] == 2 and dirs["docs"] == 1 and dirs["."] == 2, dirs   # bin.dat + empty.py 在根
assert d["top_files"][0]["path"] == "docs/readme.md", d["top_files"][:2]
assert d["truncated"] is False

text = k.format_report(d, top=5)
assert "工作区代码地图" in text and "按类型" in text and "按目录" in text

# 工具入口：显式 path / 缺省 path / 非法 path
res = k.tool_report({"path": str(root), "top": 3})
assert "工作区代码地图" in res["text"] and res["images"] == []
res2 = k.tool_report({"path": str(root / "不存在")})
assert "扫描失败" in res2["text"]
res3 = k.tool_report({})
assert res3["text"]

# 缓存：扫描 → 写入 → 读到同一份；clear 后读不到
k.store_cache(d)
assert k.cached()["root"] == d["root"]
assert k.cached(max_age=0.001) == k.cached(max_age=0.001)
k.clear_cache()
assert k.cached() == {}

# 截断保护：max_files 很小 → 必须标 truncated 且不抛
d2 = k.scan(root, max_files=1)
assert d2["truncated"] is True, d2
print("OK", d["files"], d["code_lines"], round(d["elapsed"], 3))
'''


def kernel_selftest() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="cmker_"))
    pkg = tmp / "zhuzhu_Copilot" / "core"
    pkg.mkdir(parents=True)
    (tmp / "zhuzhu_Copilot" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(KERNEL, pkg / "code_map.py")
    script = tmp / "t.py"
    script.write_text(_SELFTEST, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(tmp))
    r = subprocess.run([sys.executable, str(script)], capture_output=True,
                       text=True, env=env, cwd=str(tmp))
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    print("[5] 内核自测：%s" % (out.splitlines()[-1] if out else "(无输出)"))
    if r.returncode != 0:
        print(out[-3000:])
        raise SystemExit("内核自测失败")


if __name__ == "__main__":
    ops = build_ops()
    write_ops(ops)
    apply_to_temp(ops)
    guard_scan(ops)
    kernel_selftest()
    print("\n全部校验通过：%s" % PKG)
