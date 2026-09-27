"""一致性校验：_BUILTIN_MD_SKILLS 内置模板 vs 随包 skills/<名>/SKILL.md。

为何需要：`ensure_md_skills()` 每次启动都会把 ~/.winapp_migrator/agent/skills/<名>/SKILL.md
覆写为内置模板内容（模板是权威来源）。若只改随包文件而漏改模板，技能读到的仍是旧文本 ——
表现为「技能里写的规则 AI 完全没遵守」。本脚本按模板口径逐字重建期望文本并比对。

KNOWN_DIVERGENT：历史遗留的双源漂移（两处内容各有取舍，需逐个裁定后同步）。
列在此处不算失败；**新出现的漂移会失败**，从而保证以后改 SKILL.md 时不会漏改模板。
  - doc-gen / web-search / system-admin / skill-create：随包文件与模板长期不同步
    （模板是 AI 实际读到的版本，改动前先确认哪一侧为准再同步）。
  - browser-control / custom-ui-ux：仅存在于内置模板（无随包文件），属正常。
"""
import difflib
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from winapp_migrator.core import agent_skills

SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "src", "winapp_migrator", "skills")

KNOWN_DIVERGENT = {"doc-gen", "web-search", "system-admin", "skill-create",
                   "browser-control", "custom-ui-ux"}


def expected_text(name: str, cfg: dict) -> str:
    """按 ensure_md_skills 的模板口径生成期望文件内容"""
    return ("---\nname: " + name + "\ndescription: " + cfg["description"] + "\n---\n\n"
            + cfg["instruction"].strip() + "\n")


def main() -> int:
    bad, known = [], []
    for name, cfg in agent_skills._BUILTIN_MD_SKILLS.items():
        path = os.path.join(SKILLS_DIR, name, "SKILL.md")
        if not os.path.isfile(path):
            print(name.ljust(18), "随包文件缺失")
            (known if name in KNOWN_DIVERGENT else bad).append(name)
            continue
        with open(path, encoding="utf-8") as f:
            cur = f.read()
        exp = expected_text(name, cfg)
        if cur == exp:
            print(name.ljust(18), "一致")
            continue
        diff = [x for x in difflib.unified_diff(exp.splitlines(), cur.splitlines(), lineterm="")
                if x.startswith(("+", "-")) and not x.startswith(("+++", "---"))]
        print(name.ljust(18),
              f"不一致（模板 {len(exp)}B / 随包文件 {len(cur)}B，差异 {len(diff)} 行）")
        (known if name in KNOWN_DIVERGENT else bad).append(name)
    print("已知遗留漂移:", known or "无")
    print("新增漂移（必须同步模板）:", bad or "无")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

