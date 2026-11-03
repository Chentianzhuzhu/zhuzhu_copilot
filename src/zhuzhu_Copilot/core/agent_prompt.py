"""Agent 提示词 / 人格的分层配置中心。

分层（越靠后优先级越高，后层不可被前层覆盖）：

1. **角色层** —— 每个 Agent 自己的 ``persona``（性格）与 ``system_prompt``（系统级提示词）：
   - 默认主 Agent：设置页「提示词与人格」的两栏直接**覆盖**内置人格（整体替换）；
   - 自定义 Agent（``@agent``）：Agent 管理页逐项配置，存在 ``agents/<name>.json``；
   - 子 Agent：``subagents.json`` 的 ``persona`` / ``system_prompt``；
   - 工作流：``agent.py`` 的 ``SYSTEM_PROMPT`` / ``build_system_prompt``。
2. **全局层** —— 设置页「全局规则」：**任何角色、任何切换都追加**，不可被角色人格覆盖。
   这是用户唯一能保证「换 Agent 也生效」的位置，也是本模块存在的核心理由：
   角色人格是「整体替换型」提示词，若把全局规则塞进角色层，一旦切换 Agent 就丢失。
3. **强制层** —— ``agent_skills`` 末尾的【强制要求】兜底（既有实现，不在此模块）。

存储：``settings.json``
- ``global_system_prompt``       全局规则
- ``main_agent_persona``         默认主 Agent 性格（覆盖内置 persona）
- ``main_agent_system_prompt``   默认主 Agent 系统提示词（覆盖内置 system_prompt）
- ``custom_system_prompt``       旧键（原「补充提示词」）：读取时视作全局规则，
  保存时改写为新键 —— 旧版本的语义就是「补充到提示词末尾」，与全局规则等价。
"""

from zhuzhu_Copilot.core.i18n import tp as _tp

GLOBAL_KEY = "global_system_prompt"
MAIN_PERSONA_KEY = "main_agent_persona"
MAIN_SYSTEM_KEY = "main_agent_system_prompt"
#: 旧版「系统提示词」设置键：语义 = 追加到提示词末尾的补充，迁移为全局规则
LEGACY_GLOBAL_KEY = "custom_system_prompt"

_GLOBAL_BLOCK = (
    "\n\n【全局规则】（用户在设置页设定，对所有 Agent 一律生效；"
    "优先级高于任何角色人格与局部提示词，切换 Agent / 对话流不改变）：\n")


def _settings() -> dict:
    """读取 settings.json（懒导入：agent_skills 反向依赖本模块，避免循环导入）。"""
    try:
        from zhuzhu_Copilot.core import agent_skills
        return agent_skills.load_settings()
    except Exception:
        return {}


def _text(key: str) -> str:
    v = _settings().get(key)
    return str(v or "").strip()


def global_prompt() -> str:
    """全局规则正文（旧键非空且新键为空时按旧键迁移读取）。"""
    new = _text(GLOBAL_KEY)
    return new or _text(LEGACY_GLOBAL_KEY)


def main_persona() -> str:
    """默认主 Agent 的性格覆盖；空串 = 沿用内置。"""
    return _text(MAIN_PERSONA_KEY)


def main_system_prompt() -> str:
    """默认主 Agent 的系统提示词覆盖；空串 = 沿用内置。"""
    return _text(MAIN_SYSTEM_KEY)


def main_overrides(persona: str, system_prompt: str) -> tuple:
    """(内置 persona, 内置 system_prompt) → 应用用户覆盖后的结果。

    只替换用户真正填写的部分：留空即保持内置，避免「只想改性格却把整份
    工具方法论清空」。
    """
    return (main_persona() or persona or "", main_system_prompt() or system_prompt or "")


def global_block() -> str:
    """全局规则段落：由引擎统一追加到**任何** Agent 的系统提示词末尾。

    刻意不放进 ``agent_skills.build_system_prompt``：自定义人格 / 工作流
    ``SYSTEM_PROMPT`` 会绕过该函数（整体替换），放进去对它们完全失效。
    """
    body = global_prompt()
    if not body:
        return ""
    return _tp("prompt.block.global_rules", _GLOBAL_BLOCK) + body


def compose(persona: str, system_prompt: str) -> str:
    """把「性格」与「系统级提示词」合成单段人格文本（两段都可为空）。

    引擎的 ``persona`` 是整体替换型系统提示词，因此自定义 Agent / 子 Agent 的两栏
    必须在进入引擎前合并；分开存储只为让用户在 UI 上区分「性格」与「规则」。
    """
    return "\n\n".join(x for x in ((persona or "").strip(),
                                   (system_prompt or "").strip()) if x)
