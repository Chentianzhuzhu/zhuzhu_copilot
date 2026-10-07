# -*- coding: utf-8 -*-
"""验证「默认工作流主 Agent 的系统提示词如何切换为 English」。

链路（不依赖 GUI）：
  设置项 prompt_lang  ──> i18n.detect_prompt_lang() ──> i18n.current_prompt_lang()
   └─> agent_skills._tp(key, 中文原文) 查 locales/prompt_en_US.json
        └─> build_system_prompt() 首段即为英文语言指令 prompt.lang.directive

本脚本直接切换 i18n 提示词语言并构建 system prompt，断言：
  - zh_CN：首段为中文《【输出语言】…》指令
  - en_US：首段为英文 [Output language] … 指令，且整段主体为英文
  - 中文环境零查表（_tp 直接返回中文原文），英文环境走语言包
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from zhuzhu_Copilot.core import i18n  # noqa: E402
from zhuzhu_Copilot.core import agent_skills  # noqa: E402

ok = True


def check(label, cond, extra=""):
    global ok
    print(f"[{'PASS' if cond else 'FAIL'}] {label}"
          f"{(' | ' + str(extra)[:180]) if extra and not cond else ''}")
    ok = ok and bool(cond)


print("=== 1. 语言状态解析 ===")
i18n.set_lang(i18n.ZH_CN, persist=False, prompt_lang=i18n.ZH_CN)
zh_lang = i18n.current_prompt_lang()
print(f"  set_lang(prompt_lang=zh_CN) → current_prompt_lang() = {zh_lang}")
check("提示词语言为 zh_CN", zh_lang == i18n.ZH_CN, zh_lang)

i18n.set_lang(i18n.EN_US, persist=False, prompt_lang=i18n.EN_US)
en_lang = i18n.current_prompt_lang()
print(f"  set_lang(prompt_lang=en_US) → current_prompt_lang() = {en_lang}")
check("提示词语言切到 en_US", en_lang == i18n.EN_US, en_lang)

print("\n=== 2. 语言指令 key（提示词首段） ===")
i18n.set_lang(i18n.ZH_CN, persist=False, prompt_lang=i18n.ZH_CN)
zh_dir = agent_skills._tp("prompt.lang.directive", agent_skills._LANG_DIRECTIVE_ZH)
print(f"  zh 首段: {zh_dir[:60]}…")
check("中文下语言指令为中文（回退原文）", "输出语言" in zh_dir, zh_dir)

i18n.set_lang(i18n.EN_US, persist=False, prompt_lang=i18n.EN_US)
en_dir = agent_skills._tp("prompt.lang.directive", agent_skills._LANG_DIRECTIVE_ZH)
print(f"  en 首段: {en_dir[:80]}…")
check("★ 英文下语言指令来自 prompt_en_US.json（Output language / English）",
      "[Output language]" in en_dir and "English" in en_dir, en_dir)
check("★ 英文语言指令不再是中文", "输出语言" not in en_dir, en_dir)

print("\n=== 3. 默认工作流主 Agent 的 system prompt ===")
# 默认工作流无自定义 agent.py 人设 → 走内置 build_system_prompt（agent_engine L2012）
i18n.set_lang(i18n.ZH_CN, persist=False, prompt_lang=i18n.ZH_CN)   # 复位为中文再构建
print(f"  [debug] 构建前 current_prompt_lang() = {i18n.current_prompt_lang()}")
zh_prompt = agent_skills.build_system_prompt("")
check("中文环境：system prompt 以中文语言指令开头",
      zh_prompt.startswith(zh_dir[:20]), zh_prompt[:40])
check("中文环境：prompt 主体为中文（含「严格规则」）",
      "严格规则" in zh_prompt, "")

i18n.set_lang(i18n.EN_US, persist=False, prompt_lang=i18n.EN_US)
en_prompt = agent_skills.build_system_prompt("")
print(f"  en system prompt 长度 {len(en_prompt)}；开头 120 字：")
print("  " + en_prompt[:120].replace("\n", " / "))
check("★ 英文环境：system prompt 以英文语言指令开头",
      en_prompt.startswith("[Output language]"), en_prompt[:40])
check("★ 英文环境：system prompt 主体为英文（严格规则 → Strict rules）",
      "Strict rules" in en_prompt or "strict rules" in en_prompt, "")
check("★ 英文环境：system prompt 不再含中文「严格规则」",
      "严格规则" not in en_prompt, "")

print("\n=== 4. 与界面语言解耦 ===")
# 界面中文 + 提示词英文（用户可在设置里单独指定）
i18n.set_lang(i18n.ZH_CN, persist=False, prompt_lang=i18n.EN_US)
print(f"  ui_lang={i18n.current_lang()}  prompt_lang={i18n.current_prompt_lang()}")
check("界面中文时可单独让提示词为英文（两者解耦）",
      i18n.current_lang() == i18n.ZH_CN
      and i18n.current_prompt_lang() == i18n.EN_US)
mixed = agent_skills.build_system_prompt("")
check("★ 该组合下 system prompt 仍为英文", mixed.startswith("[Output language]"))

# 复位，避免污染本机设置（persist=False 未落盘，仅复位进程内状态）
i18n.set_lang(i18n.ZH_CN, persist=False, prompt_lang=i18n.ZH_CN)

print(f"\n{'ALL PASS' if ok else 'HAS FAILURES'}")
sys.stdout.flush()
sys.exit(0 if ok else 1)
