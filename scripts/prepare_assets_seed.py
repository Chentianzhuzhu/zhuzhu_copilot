"""构建期：把现有全部技能、Agent 插件与 Copilot 插件快照成种子目录，随安装包分发。

背景：仓库内置的 src/zhuzhu_Copilot/{skills,plugins,copilot_plugins_examples} 只是
基线，用户目录里还有自建/导入的技能、插件与 Copilot 插件
（~/.zhuzhu_Copilot/agent/skills、~/.zhuzhu_Copilot/plugins、
~/.zhuzhu_Copilot/copilot_plugins）。不打包的话，新机安装后这些资产全都不存在。
本脚本在构建期把两边合并快照到 build/assets_seed/，PyInstaller 以
skills_seed / plugins_seed / copilot_plugins_seed 平铺进安装目录，运行时首启再
同步到用户目录（agent_skills.ensure_md_skills / agent_plugins.ensure_shipped_plugins /
copilot_plugins.ensure_shipped_copilot_plugins）。

合并规则：
- 仓库内置优先；用户目录只补齐未内置的同名之外的资产（同名不覆盖内置）。
- 有效性按结构判断，不写死具体名字：
  技能 = 含 SKILL.md + frontmatter 合法 name + 正文足够长（过滤占位/测试技能）；
  插件 = 含可解析的 plugin.json 且 name 合法。
- 插件投影的技能不重复打包（插件目录本身就是该技能的唯一真源）。
- 用户目录来源的插件不打包 config.json（用户配置/密钥/本机路径）；
  仓库内置的 config.json 是默认模板，保留。
- 跳过 __pycache__ / 隐藏目录 / *.pyc / *.log 等构建垃圾。

用法：
    python scripts/prepare_assets_seed.py
"""
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zhuzhu_Copilot import app_identity            # noqa: E402
from zhuzhu_Copilot.core import agent_plugins     # noqa: E402
from zhuzhu_Copilot.core import agent_skills      # noqa: E402

SEED = ROOT / "build" / "assets_seed"
REPO = ROOT / "src" / "zhuzhu_Copilot"

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")
# 真实技能说明远长于此；低于阈值的基本是占位/测试产物（例如 `--- name: x ---\nx`）
_MIN_SKILL_BODY = 40
_JUNK_DIRS = {"__pycache__", ".git", ".venv", "venv", "node_modules", ".idea"}
_JUNK_SUFFIX = (".pyc", ".pyo", ".log")


def _skip_name(name: str) -> bool:
    return name.startswith(".") or name in _JUNK_DIRS


def _ignore(_dir, names):
    return {n for n in names
            if _skip_name(n) or n.lower().endswith(_JUNK_SUFFIX)}


def _skill_reject(d: Path) -> str:
    """技能目录不合格原因（空串 = 可打包）。"""
    f = d / "SKILL.md"
    if not f.is_file():
        return "缺少 SKILL.md"
    try:
        s = agent_skills._parse_skill_md(f.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return "SKILL.md 读取失败"
    if not s or not _NAME_RE.match(str(s.get("name") or "")):
        return "frontmatter 缺少合法 name"
    if len(str(s.get("instruction") or "")) < _MIN_SKILL_BODY:
        return "正文过短（占位/测试技能）"
    return ""


def _plugin_reject(d: Path) -> str:
    """插件目录不合格原因（空串 = 可打包）。"""
    meta = agent_plugins._plugin_meta(d)
    if not meta:
        return "plugin.json 缺失或无法解析"
    if not _NAME_RE.match(str(meta.get("name") or d.name)):
        return "plugin.json 缺少合法 name"
    return ""


def _copilot_reject(d: Path) -> str:
    """Copilot 插件目录不合格原因（空串 = 可打包）。"""
    meta = agent_plugins._plugin_meta(d)
    if not meta:
        return "plugin.json 缺失或无法解析"
    kind = str(meta.get("kind") or "").strip().lower()
    if kind not in ("copilot", "combined", ""):
        return f"kind={kind} 不是 copilot"
    if not _NAME_RE.match(str(meta.get("name") or d.name)):
        return "plugin.json 缺少合法 name"
    return ""


def _collect(roots, reject, skip_names=frozenset()):
    """按 origin 顺序收集合格目录（前者优先，同名只留第一份）。

    返回 (included, skipped, shadowed)：目录名列表 + 跳过原因 + 被内置遮蔽的同名项。
    """
    picked, skipped, shadowed = {}, [], []
    for origin, root in roots:
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            if not d.is_dir() or _skip_name(d.name):
                continue
            if d.name in skip_names:
                skipped.append(f"{origin}:{d.name}（由插件提供）")
                continue
            if d.name in picked:
                shadowed.append(d.name)
                continue
            why = reject(d)
            if why:
                skipped.append(f"{origin}:{d.name}（{why}）")
                continue
            picked[d.name] = (origin, d)
    return picked, skipped, shadowed


def _plugin_skill_names(picked: dict) -> set:
    """插件提供的技能名集合（plugin.json 的 name / skill_name）——技能快照时跳过。"""
    names = set()
    for _origin, d in picked.values():
        meta = agent_plugins._plugin_meta(d)
        names |= {str(meta.get(k) or "") for k in ("name", "skill_name")}
    names.discard("")
    return names


def _write(kind: str, picked: dict) -> None:
    dst = SEED / kind
    dst.mkdir(parents=True, exist_ok=True)
    for name, (origin, d) in picked.items():
        shutil.copytree(d, dst / name, ignore=_ignore)
        if kind in ("plugins", "copilot_plugins") and origin == "user":
            # 用户配置可能含密钥/本机路径，不随包发行（缺失时插件按内置默认工作）
            (dst / name / "config.json").unlink(missing_ok=True)


def _report(kind: str, picked: dict, skipped: list, shadowed: list) -> None:
    print(f"[{kind}] 打包 {len(picked)} 个: " + ", ".join(sorted(picked)))
    if shadowed:
        print(f"[{kind}] 同名以仓库内置为准，用户目录副本未打包: " + ", ".join(sorted(set(shadowed))))
    for s in skipped:
        print(f"[{kind}] 跳过 {s}")


def main() -> int:
    if SEED.exists():
        shutil.rmtree(SEED)
    SEED.mkdir(parents=True, exist_ok=True)

    plugins, p_skip, p_shadow = _collect(
        [("repo", REPO / "plugins"), ("user", app_identity.data_root() / "plugins")],
        _plugin_reject)
    skills, s_skip, s_shadow = _collect(
        [("repo", REPO / "skills"), ("user", agent_skills._skills_dir())],
        _skill_reject, skip_names=_plugin_skill_names(plugins))
    copilots, c_skip, c_shadow = _collect(
        [("repo", REPO / "copilot_plugins_examples"),
         ("user", app_identity.data_root() / "copilot_plugins")],
        _copilot_reject)
    if not plugins and not skills and not copilots:
        print("未收集到任何技能/插件/Copilot 插件，检查仓库 src/zhuzhu_Copilot 与用户数据目录",
              file=sys.stderr)
        return 1

    _write("plugins", plugins)
    _write("skills", skills)
    _write("copilot_plugins", copilots)

    # 快照来自用户数据目录，脚本里可能还写着旧包名：随包分发前统一改写成当前身份，
    # 否则新机加载时 ModuleNotFoundError（与 prepare_workflow_seed 同款处理）。
    fixed = (app_identity.rewrite_legacy_names_in_tree(SEED / "plugins")
             + app_identity.rewrite_legacy_names_in_tree(SEED / "skills")
             + app_identity.rewrite_legacy_names_in_tree(SEED / "copilot_plugins"))

    _report("plugins", plugins, p_skip, p_shadow)
    _report("skills", skills, s_skip, s_shadow)
    _report("copilot_plugins", copilots, c_skip, c_shadow)
    print(f"资产种子已生成: {SEED}（旧包名改写 {fixed} 个文件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
