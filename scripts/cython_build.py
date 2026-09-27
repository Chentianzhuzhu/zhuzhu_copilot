"""Cython 混淆加固：把 core/ 核心模块编译为 .pyd，生成无源码的构建树。

- 把 src/winapp_migrator/ 完整复制到 build/stage_src/winapp_migrator/
- 用 Cython 把 stage_src/winapp_migrator/core/*.py 就地编译为 .pyd，再删除对应 .py/.c
- 保留 __init__.py、skills/、workflow_templates/，以及 UI/工具层为可读 .py（加固核心逻辑）

用法：
    python scripts/cython_build.py           # 全量：复制 + 编译所有 core 模块
    python scripts/cython_build.py probe     # 仅编译 agent_json，用于快速验证管线
    python scripts/cython_build.py --only agent_json
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "winapp_migrator"
STAGE = ROOT / "build" / "stage_src"
CORE = STAGE / "winapp_migrator" / "core"
CORE_SRC = SRC / "core"

# 所有需要编译为 .pyd 的核心模块（排除 __init__.py）
CORE_MODULES = [
    "agent_browser", "agent_context", "agent_engine", "agent_feedback", "agent_find",
    "agent_git", "agent_json", "agent_llm", "agent_mcp", "agent_plugins",
    "agent_runtime", "agent_sandbox", "agent_screen", "agent_skills", "agent_subagent",
    "agent_tools", "agent_tts", "agent_workflow",
    "app_scanner", "data_dirs", "execution_guard", "fast_download", "input_guard",
    "memory_optimizer", "migration", "network_defense", "orchestrator",
    "permissions", "registry", "security", "shortcut", "uninstaller", "uwp",
]


def _setup_py(targets: list) -> str:
    lines = [
        "from setuptools import setup\n",
        "from Cython.Build import cythonize\n",
        "from setuptools.extension import Extension\n",
        "import glob, os\n",
        "mods = %r\n" % targets,
        "exts = [Extension(os.path.join('winapp_migrator', 'core', m),\n",
        "                  [os.path.join('winapp_migrator', 'core', m + '.py')])\n",
        "         for m in mods]\n",
        "setup(ext_modules=cythonize(\n",
        "    exts,\n",
        "    compiler_directives={'language_level': 3,\n",
        "                        'c_string_type': 'str', 'c_string_encoding': 'utf8'},\n",
        "    nthreads=8))\n",
    ]
    return "".join(lines)


def _probe() -> bool:
    """快速验证：编译 agent_json 到 .pyd 并成功 import。返回是否可用。"""
    import importlib.util
    target = "build/cython_probe"
    probe_dir = ROOT / target
    if probe_dir.exists():
        shutil.rmtree(probe_dir, ignore_errors=True)
    pkg = probe_dir / "winapp_migrator" / "core"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    shutil.copy(CORE_SRC / "agent_json.py", pkg / "agent_json.py")
    (probe_dir / "setup.py").write_text(_setup_py(["agent_json"]), encoding="utf-8")
    r = subprocess.run([sys.executable, "setup.py", "build_ext", "--inplace"],
                       cwd=str(probe_dir), capture_output=True, text=True)
    if r.returncode != 0:
        print("PROBE COMPILE FAILED:\n", r.stdout[-1200:], r.stderr[-800:])
        return False
    pyd = [f for f in pkg.glob("agent_json*.pyd")]
    if not pyd:
        print("PROBE: no .pyd generated")
        return False
    pkg.joinpath("agent_json.py").unlink(missing_ok=True)
    spec = importlib.util.spec_from_file_location("agent_json", str(pyd[0]))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    r = m.parse_tool_args('{"path": "a/文件夹.txt", "content": "多行\n内容"}')
    print(f"PROBE OK: {pyd[0].name} -> {r}")
    return True


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if "probe" in sys.argv[1:]:
        sys.exit(0 if _probe() else 1)

    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]

    # 1. 复制整个包到 staging，得到可读脚手架的干净副本
    if STAGE.exists():
        shutil.rmtree(STAGE, ignore_errors=True)
    shutil.copytree(SRC, STAGE / "winapp_migrator",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy(ROOT / "src" / "main.py", STAGE / "main.py")

    # 2. 编译目标 core 模块（默认全部；--only 时只编译指定模块）
    targets = [only] if only else CORE_MODULES
    targets = [t for t in targets if (CORE_SRC / f"{t}.py").is_file()]
    (CORE / "setup.py").unlink(missing_ok=True)
    (STAGE / "setup.py").write_text(_setup_py(targets), encoding="utf-8")
    r = subprocess.run([sys.executable, "setup.py", "build_ext", "--inplace"],
                       cwd=str(STAGE), capture_output=True, text=True)
    if r.returncode != 0:
        print("CYTHON BUILD FAILED:\n", r.stdout[-1500:], r.stderr[-1000:])
        sys.exit(1)

    # 3. 删除已编译模块的 .py/.c（源码不进入产物）
    for t in targets:
        (CORE / f"{t}.py").unlink(missing_ok=True)
        (CORE / f"{t}.c").unlink(missing_ok=True)
    os.remove(STAGE / "setup.py")
    for p in CORE.glob("*.so"):
        p.unlink(missing_ok=True)   # 仅 Windows，.so 是误产物
    pyd_count = len(list(CORE.glob("*.pyd")))
    print(f"Cython 完成：core/ 下 {pyd_count} 个 .pyd，对应 .py 已移除（staging: {STAGE}）")
    if only:
        return
    # 清理 staging 里的编译残留 build/ 目录
    shutil.rmtree(STAGE / "build", ignore_errors=True)


if __name__ == "__main__":
    main()