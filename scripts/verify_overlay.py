"""转译层（源码覆盖层）自检：打包态到底能不能生效。

源码级 Copilot 插件在**安装版（exe）**里靠三样东西工作，缺一样就会「装上了却没反应」：

  1. 随包源码快照 `_copilot_src/zhuzhu_Copilot/**` —— 安装目录没有源码，插入/替换类
     op 需要它当「原版基准」，导入钩子也靠它把 `__file__` 指回原版（包内资源才找得到）；
  2. 用户数据目录的 `source_overlay/` —— 插件变更的实际生效副本；
  3. `_copilot_overlay_hook.py` 导入钩子 —— 让 `zhuzhu_Copilot.*` 优先命中覆盖层。

用法：

    python scripts/verify_overlay.py                    # 体检 dist/ + 跑打包态模拟
    python scripts/verify_overlay.py --inspect DIR      # 只体检某个安装目录
    python scripts/verify_overlay.py --simulate         # 只在临时目录模拟打包态

`--simulate` 会完全隔离（临时 USERPROFILE + 临时安装目录），**不碰你正在用的覆盖层**，
所以可以随时跑：它验证的正是 exe 里才会走的那段代码路径（`sys.frozen=True`、
`app_source_root() is None`、原版内容从 `_copilot_src` 取）。
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
HOOK = SRC / "_copilot_overlay_hook.py"
DATA_DIR_NAME = ".zhuzhu_Copilot"

_OK = "  [OK]  "
_BAD = "  [!!]  "
_INFO = "  [--]  "


# ---------------------------------------------------------------------------
# 一、体检安装目录
# ---------------------------------------------------------------------------
def find_snapshot(install_dir: Path):
    """在安装目录里找随包源码快照（兼容 onedir / _internal / onefile 三种布局）。"""
    for cand in (install_dir / "_copilot_src",
                 install_dir / "_internal" / "_copilot_src"):
        if (cand / "zhuzhu_Copilot").is_dir():
            return cand
    return None


def inspect(install_dir: Path) -> bool:
    """体检一个安装目录；返回是否具备转译层机制。"""
    d = Path(install_dir)
    print("体检安装目录：%s" % d)
    if not d.is_dir():
        print(_BAD + "目录不存在")
        return False
    exe = next((p for p in d.glob("*.exe") if p.stat().st_size > 1_000_000), None)
    if exe is not None:
        print(_INFO + "主程序：%s（%.1f MB）"
              % (exe.name, exe.stat().st_size / 1048576.0))
    snap = find_snapshot(d)
    if snap is None:
        print(_BAD + "缺少 _copilot_src/zhuzhu_Copilot —— 该构建**不含**覆盖层机制，"
                     "源码级插件在它上面一定不生效")
        print(_INFO + "处理：重新打包（.\\build_release.ps1）；spec 的 datas 里已包含"
                      "源码快照，重建后即可用")
        return False
    py_files = list((snap / "zhuzhu_Copilot").rglob("*.py"))
    print(_OK + "源码快照：%s（%d 个 .py）" % (snap, len(py_files)))
    must = ["app_identity.py", "core/agent_tools.py", "ui/agent_panel.py"]
    missing = [m for m in must if not (snap / "zhuzhu_Copilot" / m).is_file()]
    if missing:
        print(_BAD + "快照缺少关键模块：%s" % "、".join(missing))
        return False
    print(_OK + "关键模块齐全（app_identity / agent_tools / agent_panel）")
    ov = Path(os.path.expanduser("~")) / DATA_DIR_NAME / "source_overlay"
    if ov.is_dir():
        mods = [str(p.relative_to(ov)).replace("\\", "/") for p in ov.rglob("*.py")]
        print(_OK + "当前转译层：%s（%d 个模块生效）" % (ov, len(mods)))
        for m in mods[:8]:
            print("        · " + m)
        if len(mods) > 8:
            print("        · …还有 %d 个" % (len(mods) - 8))
    else:
        print(_INFO + "当前转译层为空（%s）—— 没有任何源码级插件生效" % ov)
    print(_OK + "结论：该安装目录具备覆盖层机制，源码级插件可用")
    return True


# ---------------------------------------------------------------------------
# 二、打包态模拟（隔离环境，验证覆盖层优先 / 回落 / __file__ 锚点）
# ---------------------------------------------------------------------------
_PHASE = r'''
import sys
from pathlib import Path

INST = Path(r"%(inst)s")
sys.frozen = True                     # 模拟 PyInstaller 打包态
sys.executable = str(INST / "zhuzhu_Copilot.exe")
sys.path.insert(0, str(INST / "_copilot_src"))
sys.path.insert(0, str(INST))

import _copilot_overlay_hook as hook

installed = hook.install()
from zhuzhu_Copilot.core import demo

print("%(tag)s|installed=%%s|value=%%s|file=%%s"
      %% (installed, demo.VALUE, demo.__file__))
'''


def _run_phase(inst: Path, home: Path, tag: str) -> dict:
    """在隔离子进程里跑一个阶段，返回解析后的结果。"""
    script = home / ("phase_%s.py" % tag)
    script.write_text(_PHASE % {"inst": str(inst), "tag": tag}, encoding="utf-8")
    env = dict(os.environ)
    env["USERPROFILE"] = str(home)      # Windows：expanduser("~") 走这里
    env["HOME"] = str(home)
    env["PYTHONPATH"] = str(inst)
    env.pop("PYTHONHOME", None)
    r = subprocess.run([sys.executable, str(script)], capture_output=True,
                       text=True, env=env, cwd=str(home))
    out = (r.stdout or "").strip().splitlines()
    line = next((x for x in out if x.startswith(tag + "|")), "")
    if not line:
        return {"ok": False, "err": ((r.stdout or "") + (r.stderr or ""))[-800:]}
    parts = dict(p.split("=", 1) for p in line.split("|")[1:] if "=" in p)
    return {"ok": True, "installed": parts.get("installed") == "True",
            "value": parts.get("value"), "file": parts.get("file")}


def simulate() -> bool:
    """在临时目录里模拟打包态，验证覆盖层三条关键行为。"""
    base = Path(tempfile.mkdtemp(prefix="overlay_sim_"))
    home = base / "home"
    inst = base / "install"
    snap_pkg = inst / "_copilot_src" / "zhuzhu_Copilot"
    overlay_pkg = home / DATA_DIR_NAME / "source_overlay" / "zhuzhu_Copilot"
    ok = True
    try:
        (snap_pkg / "core").mkdir(parents=True)
        (snap_pkg / "__init__.py").write_text("", encoding="utf-8")
        (snap_pkg / "core" / "__init__.py").write_text("", encoding="utf-8")
        (snap_pkg / "core" / "demo.py").write_text('VALUE = "original"\n',
                                                   encoding="utf-8")
        shutil.copy2(HOOK, inst / "_copilot_overlay_hook.py")
        overlay_pkg.mkdir(parents=True)

        print("打包态模拟（隔离目录 %s）" % base)
        print(_INFO + "安装目录：%s" % inst)
        print(_INFO + "模拟数据目录：%s" % home)

        # 阶段 1：覆盖层有同名模块 → 必须命中覆盖层
        (overlay_pkg / "core").mkdir(parents=True, exist_ok=True)
        (overlay_pkg / "core" / "demo.py").write_text('VALUE = "overlay"\n',
                                                      encoding="utf-8")
        r1 = _run_phase(inst, home, "overlay")
        if r1.get("ok") and r1["installed"] and r1["value"] == "overlay":
            print(_OK + "覆盖层优先：导入到 overlay 版本（value=overlay）")
        else:
            ok = False
            print(_BAD + "覆盖层优先：失败 %s" % r1)
        if r1.get("ok") and str(r1.get("file", "")).replace("\\", "/").endswith(
                "_copilot_src/zhuzhu_Copilot/core/demo.py"):
            print(_OK + "__file__ 锚点：指回 _copilot_src 原版路径（包内资源可解析）")
        else:
            ok = False
            print(_BAD + "__file__ 锚点异常：%s" % r1.get("file"))

        # 阶段 2：覆盖层里没有该模块 → 必须回落快照
        (overlay_pkg / "core" / "demo.py").unlink()
        r2 = _run_phase(inst, home, "fallback")
        if r2.get("ok") and r2["value"] == "original":
            print(_OK + "回落原版：覆盖层没有该模块时用 _copilot_src 快照（value=original）")
        else:
            ok = False
            print(_BAD + "回落原版：失败 %s" % r2)

        # 阶段 3：覆盖层目录整个不存在 → 钩子零侵入
        shutil.rmtree(home / DATA_DIR_NAME)
        r3 = _run_phase(inst, home, "nooverlay")
        if r3.get("ok") and not r3["installed"] and r3["value"] == "original":
            print(_OK + "零侵入：没有转译层时 install() 返回 False，行为与未装插件一致")
        else:
            ok = False
            print(_BAD + "零侵入：失败 %s" % r3)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print((_OK if ok else _BAD) + "打包态模拟结论：%s"
          % ("覆盖层机制在 frozen 语义下工作正常" if ok else "存在失败项，见上"))
    return ok


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="转译层（源码覆盖层）自检")
    ap.add_argument("--inspect", metavar="DIR", nargs="?",
                    const=str(ROOT / "dist" / "zhuzhu Copilot"),
                    help="体检安装目录（省略值则用 dist/zhuzhu Copilot）")
    ap.add_argument("--simulate", action="store_true", help="只在临时目录模拟打包态")
    a = ap.parse_args()
    run_sim = a.simulate or a.inspect is None
    if a.inspect is None and not a.simulate:
        # 不带参数：两样都做（先证明机制可用，再体检真实安装目录）
        a.inspect = str(ROOT / "dist" / "zhuzhu Copilot")
    results = []
    if run_sim:
        results.append(simulate())
    if a.inspect:
        print()
        results.append(inspect(Path(a.inspect)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
