"""Agent 工作流管理（Cordis 式"一切皆可替换"）：允许用户自定义 AI Agent 核心文件并热插拔切换。

目录结构（~/.zhuzhu_Copilot/workflows/）：
  .active                  当前激活工作流名（缺省 "_default"）
  _default/                默认工作流（只读，映射内置模块，不可删除/覆盖）
  <name>/                  用户工作流目录，可含以下"核心文件"，缺失的部分回退到内置：
    agent.py              自定义 Agent：AGENT_NAME / SYSTEM_PROMPT / build_system_prompt /
                          on_task_start(engine) / on_task_end(engine)
    llm.py                自定义 LLM 客户端：LLMClient 类（base_url/api_key/model/protocol，
                          chat/chat_stream）或 make_client(config) 工厂
    tools.py              自定义工具集：TOOLS（工具定义列表）+ execute_tool(name,args,...)
    skills/               工作流专属技能目录（SKILL.md，合并进技能库）
    plugins/              工作流专属插件目录（合并进插件加载路径）
    mcp.json              {"servers":[...]} 工作流 MCP 服务器配置（合并）
    workflow.json         元数据（name/description/core_files/created/updated）
    README.md             工作流说明与契约

内置核心文件（agent_engine/agent_llm/agent_tools 等）不可删除——它们被 Cython 编译为
.pyd 加固，且 _default 工作流在管理层面受保护；用户工作流可任意创建/修改/删除。
"""

from zhuzhu_Copilot import app_identity
import copy
import importlib.util
import json
import logging
import re as _re
import shutil
import stat
import sys
import threading
import time
from pathlib import Path
from typing import Optional

_log = logging.getLogger("zhuzhu_Copilot.agent_workflow")

# 保护核心文件读写的轻量锁：多会话并发 edit_agent_file / delete_workflow 时避免交错写坏
_wf_io_lock = threading.Lock()

# 工作流根目录初始化标记：workflows_root 的 mkdir+README 每进程只做一次
_ROOT_READY = False
# 已加载的核心文件模块缓存 {(工作流名, 文件名): ((mtime_ns, size, mode), 模块)}
_MOD_CACHE: dict = {}
# workflow.json 元数据缓存 {工作流名: ((mtime_ns, size, mode), meta)}，写入时同步更新
_META_CACHE: dict = {}

# 文件指纹的复检间隔（秒）：Windows 下单次 stat ≈ 0.5ms（杀软开销），而引擎每轮都会
# 多次解析「激活工作流 / 是否存在 / 元数据 / 核心模块」——逐次 stat 会吃掉长任务每轮
# 预算的一大块（实测 ~5ms/轮）。这里对 (mtime_ns, size, mode) 指纹做短 TTL 复用：
# 应用内的写操作都会显式失效缓存（即时生效）；应用外手工改文件最多延迟这么久生效。
_FP_TTL_S = 1.0
# 指纹缓存 {路径字符串: (检查时刻, (mtime_ns, size, mode) 或 None)}；跨线程共享需加锁
_FP_CACHE: dict = {}
_FP_LOCK = threading.Lock()


def file_fingerprint(path: Path, ttl: float = _FP_TTL_S):
    """带 TTL 的 (mtime_ns, size, mode) 指纹；路径不存在返回 None。

    供本模块与其它核心模块（如 agent_subagent 的注册表）复用：热路径上的存在性/
    变更判定不必每次 stat —— TTL 内沿用上次结果，写入后调用 `touch_fingerprint`
    立即失效。指纹含 mode，便于调用方直接判定文件/目录类型。"""
    key = str(path)
    now = time.monotonic()
    with _FP_LOCK:
        hit = _FP_CACHE.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
    try:
        st = path.stat()
        fp = (st.st_mtime_ns, st.st_size, st.st_mode)
    except OSError:
        fp = None
    with _FP_LOCK:
        _FP_CACHE[key] = (now, fp)
    return fp


def touch_fingerprint(path: Path) -> None:
    """写入后立即让指纹缓存失效（下一次读取重新 stat）"""
    with _FP_LOCK:
        _FP_CACHE.pop(str(path), None)


def _dir_ok(path: Path) -> bool:
    """目录存在（带 TTL 指纹缓存，避免热路径重复 stat）"""
    fp = file_fingerprint(path)
    return bool(fp) and stat.S_ISDIR(fp[2])

# 内置核心文件清单：用户可在工作流中提供同名文件覆盖默认模块
CORE_FILES = ("agent.py", "llm.py", "tools.py", "skills", "plugins", "mcp.json")

DEFAULT_WORKFLOW = "_default"

# 线程局部当前工作流：引擎任务线程在 run() 开头设置，技能/LLM/工具/钩子按会话工作流
# 隔离（@工作流 切换后的会话走各自工作流，互不干扰并发会话）。
_WF_LOCAL = threading.local()


def set_current_workflow(name: str) -> None:
    """设置当前线程的工作流（引擎任务线程调用；空串回退全局激活工作流）"""
    _WF_LOCAL.wf = name or ""


def _current_workflow() -> str:
    return getattr(_WF_LOCAL, "wf", "") or ""


def workflows_root() -> Path:
    """工作流根目录（不存在则创建）。

    目录/README/种子初始化在进程内只做一次：本函数位于引擎每轮的工作流查找链路上
    （workflow_dir/active_workflow/is_workflow/_read_meta 都会走到），重复 mkdir +
    写 README/复制种子会让长任务每轮都产生无谓的磁盘写入与阻塞。"""
    global _ROOT_READY
    root = app_identity.data_root() / "workflows"
    if _ROOT_READY:
        return root
    try:
        root.mkdir(parents=True, exist_ok=True)
        d = root / DEFAULT_WORKFLOW
        if not d.is_dir():
            d.mkdir(parents=True, exist_ok=True)
        (d / "README.md").write_text(
            "默认工作流：映射应用内置的 AI Agent 核心模块（agent_engine / agent_llm / "
            "agent_tools / agent_skills 等），仅用于对照说明，不可删除。\n",
            encoding="utf-8")
        _seed_missing(root)   # 首启补齐随包分发的现成工作流/团队配置（失败不阻断）
        _ROOT_READY = True
    except OSError:
        pass
    return root


def _seed_dir() -> Path:
    """现成工作流种子资源目录（构建期由 scripts/prepare_workflow_seed.py 生成）：
    - 开发模式：<项目根>/build/workflows_seed
    - 打包：依次尝试 _MEIPASS/workflows_seed、<exe>/workflows_seed（随 datas 分发）"""
    if getattr(sys, "frozen", False):
        for base in (getattr(sys, "_MEIPASS", None),
                     str(Path(sys.executable).parent)):
            if not base:
                continue
            cand = Path(base) / "workflows_seed"
            if cand.is_dir():
                return cand
        return Path()
    return Path(__file__).resolve().parents[3] / "build" / "workflows_seed"


def _seed_missing(root: Path, home: Path | None = None) -> None:
    """首启种子：把随包分发的工作流/团队配置补齐到用户目录（已存在的不覆盖）。

    - 种子里的每个工作流目录（含 workflow.json）在用户目录缺失时整体复制
      （agent.py/tools.py/llm.py/mcp.json/subagents.json/skills/plugins 等全部随目录）；
    - team.json 缺失时从种子落盘，保证「安装完成即 @ 调用」全部工作流与团队。
    home: 用户主目录（测试注入隔离，缺省 Path.home()）。"""
    import shutil
    home = home if home is not None else Path.home()
    seed = _seed_dir()
    if not seed.is_dir():
        return
    try:
        for d in seed.iterdir():
            if not d.is_dir():
                continue
            if not (d / "workflow.json").is_file():
                continue
            dst = root / d.name
            if dst.is_dir():
                continue
            shutil.copytree(d, dst)
        team = seed / "team.json"
        if team.is_file():
            tgt = home / ".zhuzhu_Copilot" / "team.json"
            if not tgt.is_file():
                tgt.parent.mkdir(parents=True, exist_ok=True)
                tgt.write_text(team.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        pass


def _active_file() -> Path:
    return workflows_root() / ".active"


# 全局激活工作流的解析结果缓存（含 .active 文件读取与有效性校验）：
# 引擎每轮会调用 active_workflow 数次，逐次读盘/校验是长任务热点；TTL 内直接复用，
# 应用内切换（set_active/set_enabled/创建/删除）与元数据写入都会显式失效。
_ACTIVE_CACHE: dict = {}


def invalidate_workflow_caches() -> None:
    """失效工作流解析缓存（激活值 + 文件指纹）：写入侧调用，保证应用内改动即时生效"""
    _ACTIVE_CACHE.clear()
    with _FP_LOCK:
        _FP_CACHE.clear()


def active_workflow() -> str:
    """当前激活工作流名（缺省 _default；激活的工作流若已被禁用则回退默认）。
    引擎任务线程内优先返回其会话工作流（@切换），否则返回全局激活工作流。"""
    tl = _current_workflow()
    if tl and _dir_ok(workflow_dir(tl)) and _read_meta(tl).get("enabled", True):
        return tl
    hit = _ACTIVE_CACHE.get("name")
    if hit is not None and time.monotonic() - _ACTIVE_CACHE.get("ts", 0.0) < _FP_TTL_S:
        return hit
    try:
        name = _active_file().read_text(encoding="utf-8").strip()
    except OSError:
        name = DEFAULT_WORKFLOW
    if not name or not _dir_ok(workflow_dir(name)) or not _read_meta(name).get("enabled", True):
        name = DEFAULT_WORKFLOW
    _ACTIVE_CACHE["name"] = name
    _ACTIVE_CACHE["ts"] = time.monotonic()
    return name


def is_workflow(name: str) -> bool:
    """指定工作流是否存在且未被禁用（@工作流路由校验用）"""
    name = (name or "").strip()
    return bool(name) and _dir_ok(workflow_dir(name)) and _read_meta(name).get("enabled", True)


def resolve_workflow(name: str, fallback: str = DEFAULT_WORKFLOW) -> str:
    """把（可能已陈旧的）工作流名解析为有效工作流名：有效则原样返回，否则回退 fallback。

    用于清理"工作流已被删除/禁用"的陈旧绑定（会话状态、`.active` 等）：若不校验，
    引擎会按不存在的工作流名派生出「<名> 工作流专属助手」兜底人设，使已删除工作流的
    身份继续出现（表现为 AI 反复提及/推销某个已删工作流）。"""
    n = (name or "").strip()
    if not n:
        return fallback
    if is_default(n) or is_workflow(n):
        return n
    return fallback


def set_active(name: str) -> tuple:
    """切换激活工作流（热插拔：由调用方重建引擎生效）。禁用的工作流不可激活。返回 (ok, message)。"""
    name = (name or "").strip()
    if not name:
        return False, "工作流名不能为空"
    if not _dir_ok(workflow_dir(name)):
        return False, f"工作流不存在: {name}"
    if not _read_meta(name).get("enabled", True):
        return False, f"工作流已被禁用，请先在工作流管理中启用: {name}"
    try:
        workflows_root().mkdir(parents=True, exist_ok=True)
        _active_file().write_text(name, encoding="utf-8")
        invalidate_workflow_caches()      # 切换立即生效（解析缓存含 .active 读取）
        return True, f"已切换工作流: {name}"
    except OSError as e:
        return False, f"切换失败: {e}"


def set_enabled(name: str, enabled: bool) -> tuple:
    """启用/禁用工作流（默认工作流不可禁用；禁用激活中的工作流会回退默认）。返回 (ok, message)。"""
    if is_default(name):
        return False, "默认工作流不可禁用"
    if not _dir_ok(workflow_dir(name)):
        return False, f"工作流不存在: {name}"
    meta = _read_meta(name)
    meta["enabled"] = bool(enabled)
    _write_meta(name, meta)
    if not enabled and active_workflow() == name:
        try:
            _active_file().write_text(DEFAULT_WORKFLOW, encoding="utf-8")
        except OSError:
            pass
    invalidate_workflow_caches()          # 启停立即生效
    return True, f"已{'启用' if enabled else '禁用'}工作流: {name}"


def workflow_dir(name: str) -> Path:
    return workflows_root() / name


def is_default(name: str) -> bool:
    return (name or "").strip() == DEFAULT_WORKFLOW


def _safe_name(name: str) -> Optional[str]:
    """工作流目录名仅允许字母数字下划线中划线（拒绝路径穿越）"""
    name = (name or "").strip()
    if not name or name in (DEFAULT_WORKFLOW,):
        return None
    if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in name):
        return None
    return name


def list_workflows() -> list:
    """列出全部工作流（含激活/默认/核心文件标记）"""
    root = workflows_root()
    out = []
    active = active_workflow()
    try:
        names = sorted(p.name for p in root.iterdir() if p.is_dir())
    except OSError:
        names = []
    for n in names:
        meta = _read_meta(n)
        files = [f for f in CORE_FILES if workflow_dir(n).joinpath(f).exists()]
        out.append({
            "name": n,
            "description": meta.get("description", ""),
            "is_default": is_default(n),
            "active": n == active,
            "enabled": meta.get("enabled", True),
            "core_files": files,
            "updated": meta.get("updated", ""),
        })
    out.sort(key=lambda w: (w["is_default"], w["name"]))
    return out


def skill_states(workflow: str = None) -> dict:
    """工作流技能启用状态 {技能名: bool}；未配置时返回空 dict（全启用）。
    技能按工作流隔离：激活/切换到某工作流时，只加载该工作流启用的技能。"""
    name = workflow or active_workflow()
    meta = _read_meta(name)
    states = meta.get("skill_states")
    return states if isinstance(states, dict) else {}


def set_skill_state(workflow: str, skill: str, enabled: bool) -> tuple:
    """设置指定工作流下某技能的启用/禁用状态（写入 workflow.json 的 skill_states）。
    返回 (ok, message)；默认工作流同样支持。"""
    name = (workflow or "").strip()
    if not name or not workflow_dir(name).is_dir():
        return False, f"工作流不存在: {name}"
    skill = (skill or "").strip()
    if not skill:
        return False, "技能名不能为空"
    meta = _read_meta(name)
    states = meta.get("skill_states")
    if not isinstance(states, dict):
        states = {}
    states[skill] = bool(enabled)
    meta["skill_states"] = states
    meta["updated"] = time.strftime("%Y-%m-%d %H:%M")
    _write_meta(name, meta)
    try:
        from zhuzhu_Copilot.core import agent_skills
        agent_skills.invalidate_skills_cache()
    except Exception:
        pass
    return True, f"已{'启用' if enabled else '禁用'}技能「{skill}」（工作流 {name}）"


def _read_meta(name: str) -> dict:
    """读取工作流元数据 workflow.json（带指纹缓存，返回深拷贝）。

    引擎每轮都会经 skill_states/active_workflow 读它（长任务热点），故按
    (mtime, size, mode) 缓存解析结果，指纹本身走 `file_fingerprint` 的短 TTL 复用
    （Windows 下单次 stat ≈ 0.5ms，逐次复查会吃掉每轮预算）；写入侧 _write_meta
    同步刷新缓存，用户改动即时生效。
    返回深拷贝：调用方（set_skill_state/set_enabled 等）会就地修改后回写。"""
    f = workflow_dir(name) / "workflow.json"
    fp = file_fingerprint(f)
    if fp is None:
        _META_CACHE.pop(name, None)
        return {}
    hit = _META_CACHE.get(name)
    if hit is not None and hit[0] == fp:
        return copy.deepcopy(hit[1])
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
    except Exception:
        data = {}
    _META_CACHE[name] = (fp, data)
    return copy.deepcopy(data)


def _write_meta(name: str, meta: dict) -> None:
    try:
        d = workflow_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        f = d / "workflow.json"
        f.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        st = f.stat()
        _META_CACHE[name] = ((st.st_mtime_ns, st.st_size, st.st_mode), copy.deepcopy(meta))
        touch_fingerprint(f)              # 指纹缓存同刷：改动即时生效
        touch_fingerprint(d)
    except OSError:
        _META_CACHE.pop(name, None)


def _template_dir() -> Path:
    """内置工作流模板资源目录：
    - 开发模式：core/workflow_templates
    - 打包（onedir/onefile）：依次尝试 _MEIPASS/workflow_templates、<exe>/_internal/workflow_templates、<exe>/workflow_templates"""
    if getattr(sys, "frozen", False):
        for base in (getattr(sys, "_MEIPASS", None),
                     str(Path(sys.executable).parent / "_internal"),
                     str(Path(sys.executable).parent)):
            if not base:
                continue
            cand = Path(base) / "workflow_templates"
            if cand.is_dir():
                return cand
        return Path()
    return Path(__file__).resolve().parent / "workflow_templates"


# ------------------------------------------------------------
# 内置工作流预设：随包提供的一键创建工作流（核心文件 + 多个注册式子 Agent）
# 预设资源位于 workflow_templates/presets/<id>/，用 preset.json 自描述（不硬编码清单）：
#   {
#     "id": "sansheng_liubu",
#     "display_name": "三省六部制度",
#     "description": "用途一句话",
#     "workflow_name": "sansheng_liubu",   // 创建出的工作流目录名（可选，缺省用 id）
#     "agents": ["门下省", "尚书省", ...]   // 编队成员展示名（仅用于说明）
#   }
# 目录内除 preset.json 外的全部文件/目录按原样复制进新工作流（agent.py / tools.py /
# subagents.json / skills/ / plugins/ / mcp.json 等），新增预设只需新增目录。
# ------------------------------------------------------------
def _preset_dir() -> Path:
    """内置工作流预设资源目录（随 workflow_templates 一起打包）"""
    base = _template_dir()
    if not base or str(base) in ("", "."):
        return Path()
    return base / "presets"


def _read_preset(pd: Path) -> dict:
    """读取单个预设目录的 preset.json（缺省用目录名兜底）"""
    try:
        raw = (pd / "preset.json").read_text(encoding="utf-8")
        data = json.loads(raw)
        data = data if isinstance(data, dict) else {}
    except Exception:
        data = {}
    pid = str(data.get("id") or pd.name).strip()
    data["id"] = pid
    data.setdefault("display_name", pid)
    data.setdefault("description", "")
    data.setdefault("workflow_name", pid)
    data.setdefault("agents", [])
    return data


def builtin_presets() -> list:
    """全部内置工作流预设（按名称排序）；无预设资源时返回空列表。"""
    root = _preset_dir()
    if not root or not root.is_dir():
        return []
    out = []
    try:
        for pd in sorted(p for p in root.iterdir() if p.is_dir()):
            if (pd / "preset.json").is_file():
                out.append(_read_preset(pd))
    except OSError:
        return []
    return out


def _preset_by_id(preset_id: str) -> Optional[dict]:
    pid = (preset_id or "").strip()
    for p in builtin_presets():
        if p.get("id") == pid:
            return p
    return None


def list_builtin_workflows() -> list:
    """列内置预设 + 是否已创建（同名工作流已存在即为已创建）。"""
    out = []
    for p in builtin_presets():
        wf_name = str(p.get("workflow_name") or p.get("id") or "")
        out.append({
            "id": p.get("id", ""),
            "display_name": p.get("display_name", ""),
            "description": p.get("description", ""),
            "workflow_name": wf_name,
            "agents": list(p.get("agents") or []),
            "created": bool(wf_name) and workflow_dir(wf_name).is_dir(),
        })
    return out


def create_builtin_workflow(preset_id: str, name: str = "",
                            description: str = "", on_status=None) -> tuple:
    """从内置预设一键创建工作流（复制预设资源 + 登记元数据）。返回 (ok, message)。

    name 留空用预设的 workflow_name；已有同名工作流时拒绝（不覆盖用户数据）。
    复制完成后立即失效技能缓存，使预设自带的技能/子 Agent 生效（switch 激活后可用）。
    """
    preset = _preset_by_id(preset_id)
    if not preset:
        ids = ", ".join(p.get("id", "") for p in builtin_presets()) or "（无）"
        return False, f"内置工作流预设不存在: {preset_id}（可用: {ids}）"
    safe = _safe_name(name or str(preset.get("workflow_name") or preset.get("id") or ""))
    if not safe:
        return False, "工作流名仅允许字母/数字/下划线/中划线，且不能为 _default"
    d = workflow_dir(safe)
    if d.exists():
        return False, f"工作流已存在: {safe}（如需重建请先删除或换 name）"
    src = _preset_dir() / str(preset.get("id"))
    if not src.is_dir():
        return False, f"预设资源缺失: {src}"
    _report(on_status, 10, f"正在创建内置工作流「{preset.get('display_name') or preset_id}」…")
    try:
        d.mkdir(parents=True, exist_ok=True)
        for item in src.iterdir():
            if item.name == "preset.json":
                continue
            dst = d / item.name
            if item.is_dir():
                shutil.copytree(item, dst)
            else:
                shutil.copy2(item, dst)
        core_files = [f for f in CORE_FILES if d.joinpath(f).exists()]
        meta = {
            "name": safe,
            "description": description or str(preset.get("description") or ""),
            "enabled": True,
            "preset": str(preset.get("id") or ""),
            "core_files": core_files,
            "created": time.strftime("%Y-%m-%d %H:%M"),
            "updated": time.strftime("%Y-%m-%d %H:%M"),
        }
        _write_meta(safe, meta)
        _report(on_status, 90, "正在登记技能与子 Agent…")
        try:
            from zhuzhu_Copilot.core import agent_skills
            agent_skills.invalidate_skills_cache()   # 预设自带技能立即并入技能库
        except Exception:
            pass
        n_sub = 0
        try:
            from zhuzhu_Copilot.core import agent_subagent
            n_sub = len(agent_subagent.registered_subagents(safe))
        except Exception:
            pass
        _report(on_status, 100, "内置工作流创建完成")
        invalidate_workflow_caches()   # 新建的核心文件/技能立即可被加载
        msg = (f"已创建内置工作流「{safe}」（{preset.get('display_name') or preset_id}）"
               f"，核心文件: {', '.join(core_files) or '—'}，注册式子 Agent {n_sub} 个。"
               f"调用 switch_workflow(name=\"{safe}\") 即可激活生效。")
        return True, msg
    except OSError as e:
        try:
            shutil.rmtree(d)
        except OSError:
            pass
        return False, f"创建内置工作流失败: {e}"


def create_workflow(name: str, description: str = "", files: list = None) -> tuple:
    """创建用户工作流（复制模板核心文件）。files 为空则生成全部核心文件；
    指定 files（如 ["tools.py"]）则只创建单核心文件——支持"加入默认工作流"式增量定制。
    从内置预设创建请用 create_builtin_workflow（含注册式子 Agent 与脚本）。
    返回 (ok, message)。"""
    safe = _safe_name(name)
    if not safe:
        return False, "工作流名仅允许字母/数字/下划线/中划线，且不能为 _default"
    d = workflow_dir(safe)
    if d.exists():
        return False, f"工作流已存在: {safe}"
    try:
        d.mkdir(parents=True, exist_ok=True)
        tpl = _template_dir()
        want = files or list(CORE_FILES)
        for f in want:
            src = tpl / f
            dst = d / f
            if src.is_file():
                shutil.copy2(src, dst)
            elif src.is_dir():
                shutil.copytree(src, dst)
            else:
                # 模板缺失的目录类核心文件：创建空目录说明
                dst.mkdir(parents=True, exist_ok=True)
        _write_meta(safe, {
            "name": safe,
            "description": description or "",
            "core_files": want,
            "created": time.strftime("%Y-%m-%d %H:%M"),
            "updated": time.strftime("%Y-%m-%d %H:%M"),
        })
        invalidate_workflow_caches()   # 新建的核心文件立即可被加载
        return True, f"已创建工作流 {safe}（核心文件: {', '.join(want)}）"
    except OSError as e:
        try:
            shutil.rmtree(d)
        except OSError:
            pass
        return False, f"创建工作流失败: {e}"


def delete_workflow(name: str) -> tuple:
    """删除用户工作流；默认工作流 / 当前正在使用（激活）的工作流拒绝删除。
    返回 (ok, message)。"""
    if is_default(name):
        return False, "默认工作流不可删除"
    if name == active_workflow():
        return False, f"工作流「{name}」正在使用中，请先切换到其它工作流再删除"
    d = workflow_dir(name)
    if not d.is_dir():
        return False, f"工作流不存在: {name}"
    with _wf_io_lock:
        try:
            shutil.rmtree(d)
            invalidate_workflow_caches()   # 删除立即生效（存在性缓存随之刷新）
            return True, f"已删除工作流: {name}"
        except OSError as e:
            return False, f"删除失败: {e}"


def read_core_file(name: str, file: str) -> tuple:
    """读取工作流核心文件内容。返回 (ok, content or message)。"""
    if file not in CORE_FILES:
        return False, f"核心文件只能是: {', '.join(CORE_FILES)}"
    if file in ("skills", "plugins"):
        return False, f"{file} 为目录，请用 inspect_workflow 查看"
    if not _safe_name(name) and not is_default(name):
        return False, "非法工作流名"
    p = workflow_dir(name) / file
    if not p.is_file():
        return False, f"{name}/{file} 不存在（该模块使用内置默认实现）"
    try:
        return True, p.read_text(encoding="utf-8")
    except OSError as e:
        return False, f"读取失败: {e}"


def write_core_file(name: str, file: str, content: str) -> tuple:
    """写入/更新工作流核心文件。默认工作流同样允许（添加/覆盖用户核心文件覆盖层），
    内置编译核心模块（agent_engine/agent_llm/agent_tools 等）本身不可删除。返回 (ok, message)。"""
    if file not in CORE_FILES:
        return False, f"核心文件只能是: {', '.join(CORE_FILES)}"
    if file in ("skills", "plugins"):
        return False, f"{file} 为目录，不支持整体写入"
    if not _safe_name(name) and not is_default(name):
        return False, "非法工作流名"
    d = workflow_dir(name)
    if not d.is_dir():
        return False, f"工作流不存在: {name}"
    with _wf_io_lock:   # 多会话并发写同一工作流核心文件时避免交错写坏（write+meta 原子）
        try:
            p = d / file
            if file == "mcp.json":
                data = json.loads(content or "{}")  # 校验 JSON
                if not isinstance(data, dict) or not isinstance(data.get("servers"), list):
                    return False, "mcp.json 需为 {\"servers\": [...]} 结构"
                content = json.dumps(data, ensure_ascii=False, indent=2)
            p.write_text(content, encoding="utf-8")
            touch_fingerprint(p)          # 核心文件改动即时生效（指纹缓存同刷）
            meta = _read_meta(name)
            meta["updated"] = time.strftime("%Y-%m-%d %H:%M")
            if file not in meta.get("core_files", []):
                meta["core_files"] = list(meta.get("core_files", [])) + [file]
            _write_meta(name, meta)
            return True, f"已写入 {name}/{file}"
        except OSError as e:
            return False, f"写入失败: {e}"


def add_core_file(name: str, file: str) -> tuple:
    """为已存在工作流添加单个核心文件（复制模板；已存在则提示）。
    默认工作流同样适用——即在内置工作流中添加用户覆盖核心文件。返回 (ok, message)。"""
    if file not in CORE_FILES:
        return False, f"核心文件只能是: {', '.join(CORE_FILES)}"
    if not _safe_name(name) and not is_default(name):
        return False, "非法工作流名"
    d = workflow_dir(name)
    if not d.is_dir():
        return False, f"工作流不存在: {name}"
    dst = d / file
    if dst.exists():
        return False, f"{name}/{file} 已存在，请用编辑修改或先删除"
    tpl = _template_dir()
    src = tpl / file
    try:
        if src.is_file():
            shutil.copy2(src, dst)
        elif src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.mkdir(parents=True, exist_ok=True)
        meta = _read_meta(name)
        meta["updated"] = time.strftime("%Y-%m-%d %H:%M")
        if file not in meta.get("core_files", []):
            meta["core_files"] = list(meta.get("core_files", [])) + [file]
        _write_meta(name, meta)
        touch_fingerprint(dst)   # 新核心文件立即可被加载（指纹缓存同刷）
        return True, f"已添加核心文件 {name}/{file}（可编辑后 switch 生效）"
    except OSError as e:
        return False, f"添加失败: {e}"


def delete_core_file(name: str, file: str) -> tuple:
    """删除工作流中的单个核心文件（回退内置默认；默认工作流同样允许，内置模块不受影响）。"""
    if file not in CORE_FILES or file in ("skills", "plugins"):
        return False, f"仅支持删除文件型核心文件: agent.py / llm.py / tools.py / mcp.json"
    if not _safe_name(name) and not is_default(name):
        return False, "非法工作流名"
    try:
        (workflow_dir(name) / file).unlink(missing_ok=True)
        touch_fingerprint(workflow_dir(name) / file)   # 删除立即生效（回退内置默认）
        return True, f"已删除 {name}/{file}（该模块回退到内置默认实现）"
    except OSError as e:
        return False, f"删除失败: {e}"


# ------------------------------------------------------------
# 运行时加载：把用户工作流核心文件接到内置 Agent
# ------------------------------------------------------------
def _load_module(name: str, file: str):
    """用 importlib 动态加载工作流核心 .py 文件；失败返回 None。

    缓存策略：按 (mtime, size) 记忆已加载模块 —— 引擎每轮构建提示词都会取
    agent_hooks（长任务里等于每轮重新 exec 一遍 agent.py + 依赖检查，实测 6ms/轮），
    内容未变时直接复用同一模块对象；文件被改写（热更新）或删除时**立即**按新内容
    重新加载（此处刻意不做 TTL：用户在工作流里改 agent.py/tools.py 后立即对话就要
    生效，一次 stat 的开销换语义确定性）。"""
    p = workflow_dir(name) / file
    try:
        st = p.stat()
    except OSError:
        _MOD_CACHE.pop((name, file), None)
        return None
    if not stat.S_ISREG(st.st_mode):
        _MOD_CACHE.pop((name, file), None)
        return None
    fp = (st.st_mtime_ns, st.st_size)
    hit = _MOD_CACHE.get((name, file))
    if hit is not None and hit[0] == fp:
        return hit[1]
    try:
        mod_name = f"zhuzhu_Copilot_wf_{name.replace('-', '_')}_{file[:-3]}"
        sys.modules.pop(mod_name, None)   # 清理旧模块，防止反复热加载时对象累积
        # 自定义代码可带第三方 import：加载前先确保该工作流 requirements.txt 依赖已安装
        try:
            from zhuzhu_Copilot.core import agent_deps
            agent_deps.ensure_dir_deps(p.parent)
        except Exception:
            pass
        spec = importlib.util.spec_from_file_location(mod_name, p)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        _MOD_CACHE[(name, file)] = (fp, mod)
        return mod
    except Exception as e:
        # 核心文件 import/exec 阶段失败（语法错/依赖缺失/注入踩踏）：不再静默忽略，
        # 记录告警便于排查"自定义未生效"的隐蔽状态（调用方回退内置默认实现）。
        _log.warning("加载工作流核心文件失败: %s/%s -> %s", name, file, e)
        _MOD_CACHE.pop((name, file), None)
        return None


class _ErrorGuardClient:
    """自定义工作流 LLM 客户端的外层防护（代理）。

    拦截上游 HTTPError（尤其 400）：读取完整响应体并转为 AgentLLMError(完整诊断)，
    让对话页显示完整报错，而不是 urllib 默认的 "HTTP Error 400: Bad Request"。
    其余属性读取/写入与方法调用全部透传内层客户端，不影响自定义实现
    （包括上层设置的 effort_params / reasoning_effort 等力度参数）。
    """
    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __setattr__(self, name, value):
        if name == "_inner":
            super().__setattr__(name, value)
        else:
            setattr(self._inner, name, value)

    def _guard(self, phase: str, messages: list, exc: Exception):
        import urllib.error
        from zhuzhu_Copilot.core import agent_llm
        if not isinstance(exc, urllib.error.HTTPError):
            return exc
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        if exc.code == 400:
            payload = {"model": getattr(self._inner, "model", ""),
                       "messages": messages or []}
            return agent_llm.AgentLLMError(agent_llm._log_rejected(
                phase, getattr(self._inner, "base_url", ""), payload,
                exc.code, body))
        return agent_llm.AgentLLMError(agent_llm._humanize_http_error(exc.code, body))

    def chat_stream(self, messages, **kwargs):
        try:
            return self._inner.chat_stream(messages, **kwargs)
        except Exception as e:
            raise self._guard("chat_stream", messages, e) from None

    def chat(self, messages, **kwargs):
        try:
            return self._inner.chat(messages, **kwargs)
        except Exception as e:
            raise self._guard("chat", messages, e) from None


def _custom_llm_untouched(name: str) -> bool:
    """用户工作流的 llm.py 是否仍为随包模板原样（未定制）：归一化换行后逐字节比较。

    模板 llm.py 仅是「最小 OpenAI 兼容 Chat」实现：固定走 /chat/completions、
    忽略 protocol 设置、不做 tool_calls 配对修复，且不支持 Responses API。
    内容一致说明用户未定制客户端 → 回退内置 agent_llm.LLMClient（支持
    chat/responses 双协议 + 配对修复 + 完整诊断），避免固定发 chat 格式撞上游 400。"""
    try:
        p = workflow_dir(name) / "llm.py"
        if not p.is_file():
            return True
        tpl = _template_dir() / "llm.py"
        if not tpl.is_file():
            return True   # 模板缺失（打包异常）：保守视为未定制，用内置兜底
        def _norm(b: bytes) -> bytes:
            return b.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        return _norm(p.read_bytes()) == _norm(tpl.read_bytes())
    except Exception:
        return False


def load_llm_client(config: dict, workflow: str = None):
    """按指定（或当前激活）工作流选择 LLM 客户端：
    - 用户 llm.py 提供 LLMClient 类 → 用它实例化（base_url/api_key/model/protocol）
    - 提供 make_client(config) 工厂 → 调用之
    - 否则用内置 agent_llm.LLMClient
    返回 (client 对象, 来源名)。
    """
    from zhuzhu_Copilot.core import agent_llm
    name = workflow or active_workflow()
    mod = _load_module(name, "llm.py")
    # 超时配置（秒，可选）：由 load_model_config 从 settings.json 带出，无则交给客户端默认
    def _timeout_kwargs() -> dict:
        kw = {}
        for k in ("timeout", "idle_timeout"):
            v = (config or {}).get(k)
            if isinstance(v, (int, float)) and v > 0:
                kw[k] = float(v)
        return kw

    def _base_kwargs() -> dict:
        return {
            "base_url": (config or {}).get("base_url"),
            "api_key": (config or {}).get("api_key"),
            "model": (config or {}).get("model"),
            "protocol": (config or {}).get("protocol", "chat"),
        }
    # 自定义 llm.py 与随包模板一致（未定制）：跳过模板客户端。模板固定发 chat 格式、
    # 忽略 protocol（用户配置 responses 时会被架空且不做 tool 配对修复），
    # 直接回退内置客户端以完整支持 chat/responses 双协议。
    # 来源标记 DEFAULT_WORKFLOW（而非工作流名）：让调用方（run_agent_llm 等）据
    # _src == 目标工作流 判定"该工作流是否提供了专属 client"——未定制即沿用传入
    # client（主 Agent/团队已构造好的有效客户端），避免每次派发用 settings 默认
    # 重建而覆盖主 Agent 当前会话所选的服务商/模型，导致成员开工失败/配置错位。
    if _custom_llm_untouched(name):
        try:
            return agent_llm.LLMClient(**_base_kwargs(), **_timeout_kwargs()), DEFAULT_WORKFLOW
        except TypeError:
            return agent_llm.LLMClient(**_base_kwargs()), DEFAULT_WORKFLOW
    if mod is not None:
        try:
            if hasattr(mod, "make_client"):
                client = mod.make_client(config or {})
                if client is not None:
                    return _ErrorGuardClient(client), name
            cls = getattr(mod, "LLMClient", None)
            if cls is not None:
                try:
                    client = cls(**_base_kwargs(), **_timeout_kwargs())
                except TypeError:
                    # 旧签名自定义客户端未声明超时参数：回退基础参数重试
                    client = cls(**_base_kwargs())
                return _ErrorGuardClient(client), name
        except Exception:
            pass
    try:
        client = agent_llm.LLMClient(**_base_kwargs(), **_timeout_kwargs())
    except TypeError:
        client = agent_llm.LLMClient(**_base_kwargs())
    return client, DEFAULT_WORKFLOW


def apply_tools(workflow: str = None):
    """把指定（或当前激活）工作流 tools.py 的自定义工具注册进内置 agent_tools（同名覆盖+新增），
    并同步注册工作流技能目录到 agent_skills。返回 (注册工具数, 来源名)。"""
    from zhuzhu_Copilot.core import agent_tools
    name = workflow or active_workflow()
    mod = _load_module(name, "tools.py")
    if mod is None:
        return 0, DEFAULT_WORKFLOW
    tools = getattr(mod, "TOOLS", None)
    handler = getattr(mod, "execute_tool", None)
    if not tools:
        return 0, DEFAULT_WORKFLOW
    try:
        from zhuzhu_Copilot.core import agent_skills
        agent_skills.set_extra_skill_dirs(extra_skill_dirs(name), workflow=name)
    except Exception:
        pass
    return agent_tools.register_custom_tools(tools, handler, source=name)


def agent_hooks(workflow: str = None):
    """返回指定（或当前激活）工作流 agent.py 的定制钩子（缺省用内置）。返回 dict。"""
    name = workflow or active_workflow()
    mod = _load_module(name, "agent.py")
    if mod is None:
        return {"name": name, "mod": None}
    return {"name": name, "mod": mod}


def workflow_extra_whitelist(workflow: str = None) -> set:
    """工作流 tools.py 声明 SUB_AGENT_ALLOWED 后，其自定义工具可进入该工作流的子 Agent 工具集。

    专属能力管理：每个工作流可独立决定「哪些自定义工具交给本工作流的子 Agent 用」，
    未声明或值为空则子 Agent 仍只用内置 SUB_AGENT_WHITELIST（向后兼容）。"""
    name = workflow or active_workflow()
    mod = _load_module(name, "tools.py")
    if mod is None:
        return set()
    extra = getattr(mod, "SUB_AGENT_ALLOWED", None)
    if isinstance(extra, (tuple, list, set, frozenset)):
        return {str(x).strip() for x in extra if str(x).strip()}
    return set()


def _agent_name(workflow: str) -> str:
    """工作流 agent.py 定义的 AGENT_NAME（缺省用工作流名派生）"""
    try:
        mod = agent_hooks(workflow).get("mod")
        if mod is not None:
            n = getattr(mod, "AGENT_NAME", None)
            if isinstance(n, str) and n.strip():
                return n.strip()
    except Exception:
        pass
    return workflow or DEFAULT_WORKFLOW


def _workflow_skill_names(workflow: str) -> list:
    """工作流 skills/ 目录下的技能名（SKILL.md frontmatter name，解析失败用目录名）"""
    d = workflow_dir(workflow) / "skills"
    if not d.is_dir():
        return []
    names = []
    try:
        for sd in sorted(p for p in d.iterdir() if p.is_dir()):
            md = sd / "SKILL.md"
            if md.is_file():
                names.append(_skill_dir_name(sd.name))
    except OSError:
        pass
    return names


def _workflow_plugin_names(workflow: str) -> list:
    """工作流 plugins/ 目录下的插件名"""
    d = workflow_dir(workflow) / "plugins"
    if not d.is_dir():
        return []
    try:
        return sorted(p.name for p in d.iterdir() if p.is_dir())
    except OSError:
        return []


def _workflow_mcp_names(workflow: str) -> list:
    """工作流 mcp.json 配置的服务器名"""
    try:
        return [str(s.get("name")) for s in mcp_config(workflow) if s.get("name")]
    except Exception:
        return []


def workflow_agents() -> list:
    """列出可被主 Agent 在当前对话内调用的其他工作流 Agent 能力：
    返回已启用且非默认工作流的 {name/agent_name/description/skills/tools/mcp/plugins}。
    供主 Agent 按能力描述选择合适的 agent 切换调用。"""
    out = []
    for w in list_workflows():
        if w["is_default"] or not w.get("enabled", True):
            continue
        name = w["name"]
        tools = ["tools.py（自定义工具集）"] if (workflow_dir(name) / "tools.py").is_file() else []
        out.append({
            "name": name,
            "agent_name": _agent_name(name),
            "description": w.get("description", ""),
            "skills": _workflow_skill_names(name),
            "tools": tools,
            "mcp": _workflow_mcp_names(name),
            "plugins": _workflow_plugin_names(name),
        })
    return out


def extra_skill_dirs(workflow: str = None) -> list:
    """指定（或当前激活）工作流 skills/ 目录列表（用于技能库合并）"""
    name = workflow or active_workflow()
    d = workflow_dir(name) / "skills"
    return [d] if d.is_dir() else []


def extra_plugin_dirs(workflow: str = None) -> list:
    """指定（或当前激活）工作流 plugins/ 目录列表（用于插件加载路径合并）"""
    name = workflow or active_workflow()
    d = workflow_dir(name) / "plugins"
    return [d] if d.is_dir() else []


def mcp_config(workflow: str = None) -> list:
    """指定（或当前激活）工作流 mcp.json 的 servers 配置（合并进 MCP 服务器列表）"""
    name = workflow or active_workflow()
    try:
        f = workflow_dir(name) / "mcp.json"
        if f.is_file():
            data = json.loads(f.read_text(encoding="utf-8"))
            return data.get("servers", []) if isinstance(data, dict) else []
    except Exception:
        pass
    return []


def allowed_mcp_servers(workflow: str = None) -> set:
    """指定（或当前激活）工作流允许使用的 MCP 服务器名集合：
    全局 mcp_servers.json 中未绑定（=全局）或绑定到该工作流的服务器 + 该工作流 mcp.json 自带服务器。
    引擎据此裁剪暴露的 MCP 工具，实现 MCP server 按工作流隔离。
    MCP 能力关闭 → 返回空集；插件能力关闭 → 剔除插件登记的 MCP 服务器。"""
    from zhuzhu_Copilot.core import agent_skills
    if not agent_skills.cap_enabled("mcp"):
        return set()
    name = workflow or active_workflow()
    allowed = set()
    try:
        for srv in agent_skills.load_mcp_servers():
            sname = srv.get("name")
            if sname and agent_skills.resource_allowed("mcp", sname, name):
                allowed.add(sname)
    except Exception:
        pass
    for srv in mcp_config(name):
        if srv.get("name"):
            allowed.add(srv.get("name"))
    if not agent_skills.cap_enabled("plugin"):
        from zhuzhu_Copilot.core import agent_plugins
        allowed -= agent_plugins.plugin_mcp_names()
    return allowed


def allowed_plugins(workflow: str = None) -> list:
    """指定（或当前激活）工作流允许使用的插件列表（启用且未绑定/绑定到该工作流）。
    插件登记的技能与 MCP 服务器已分别按工作流绑定生效，此处供管理与排查。"""
    from zhuzhu_Copilot.core import agent_skills
    from zhuzhu_Copilot.core import agent_plugins
    name = workflow or active_workflow()
    out = []
    for p in agent_plugins.list_plugins():
        if not p.get("enabled", True):
            continue
        if agent_skills.resource_allowed("plugin", p.get("name"), name):
            out.append(p)
    return out


def inspect_workflow(name: str) -> dict:
    """返回工作流结构详情（文件存在性/大小/内容摘要）"""
    d = workflow_dir(name)
    if not d.is_dir():
        return {"error": f"工作流不存在: {name}"}
    files = {}
    for f in CORE_FILES:
        p = d / f
        if p.is_file():
            files[f] = {"type": "file", "size": p.stat().st_size,
                        "head": p.read_text(encoding="utf-8", errors="replace")[:200]}
        elif p.is_dir():
            items = sorted(x.name for x in p.iterdir())
            files[f] = {"type": "dir", "children": items[:50]}
    meta = _read_meta(name)
    subs = []
    try:
        from zhuzhu_Copilot.core import agent_subagent
        subs = [s["name"] for s in agent_subagent.registered_subagents(name)]
    except Exception:
        subs = []
    return {"name": name, "is_default": is_default(name),
            "active": name == active_workflow(),
            "meta": meta, "files": files, "subagents": subs}


# ------------------------------------------------------------
# 自然语言生成工作流（真实 AI 调用）
# ------------------------------------------------------------
_CORE_CONTRACT = """各核心文件接口契约：
- agent.py: 可定义 AGENT_NAME / SYSTEM_PROMPT(str) / build_system_prompt(agent_name, extra_skills)->str /
  on_task_start(engine) / on_task_end(engine)，整体替换 Agent 人格与生命周期钩子
- llm.py: LLMClient 类（__init__(base_url,api_key,model,timeout=60,protocol='chat') +
  chat_stream(messages,tools=None,tool_choice='auto',on_delta=None,on_reasoning=None,stop=None)->dict +
  chat(messages,max_tokens=1024,timeout=60.0,stop=None)->dict），整体替换 LLM 客户端
- tools.py: TOOLS（OpenAI function schema 列表）+ execute_tool(name,args,allow_dangerous=False)->{"text","images"}
- mcp.json: {"servers":[...]}"""


def _ai_generate_workflow(desc: str, want: list) -> dict:
    """调用当前配置的 LLM 生成工作流设计 JSON（真实 API，OpenAI 兼容）"""
    from zhuzhu_Copilot.core import agent_llm
    cfg = agent_llm.load_model_config()
    client = agent_llm.LLMClient(cfg.get("base_url"), cfg.get("api_key"),
                                 cfg.get("model") or agent_llm.DEFAULT_MODEL)
    sys_p = (
        "你是 Cordis 工作流设计专家。根据用户自然语言描述，设计一个自定义 AI Agent 工作流，"
        "输出严格 JSON（不要 markdown 代码块包裹），字段如下：\n"
        "{\n"
        '  "name": "工作流名（英文，字母数字下划线连字符，≤40字符）",\n'
        '  "description": "一句话用途简介（中文）",\n'
        '  "files": {\n'
        '    "agent.py": "完整可运行代码（不满足契约或不需要则省略）",\n'
        '    "llm.py": "完整可运行代码（省略则用默认 LLM 客户端）",\n'
        '    "tools.py": "完整可运行代码（省略则用默认工具集）",\n'
        '    "skills": "工作流专属技能文件（SKILL.md 格式，含 --- frontmatter --- + instruction，'
        '省略则无专属技能）",\n'
        '    "mcp.json": "{\\"servers\\":[...]}"（省略则为空）\n'
        "  }\n"
        "}\n"
        f"{_CORE_CONTRACT}\n"
        "skills 字段为一个或多个 YAML 风格 SKILL.md 文档（用 `---` 分割多个技能），"
        "每个技能必须包含 `--- name: xxx\\ndescription: xxx\\n---\\n技能指令` 格式。\n"
        "只输出用户要求生成的对应文件；代码必须真实可运行、无 mock、无占位 TODO。"
    )
    try:
        # 显式超时（秒）：防止服务商无响应时线程无限阻塞，保证"卡死"可被兜底中断
        resp = client.chat([{"role": "system", "content": sys_p},
                            {"role": "user", "content": desc}],
                           max_tokens=4096, timeout=90)
        text = (resp or {}).get("text") or ""
    except Exception as e:
        raise RuntimeError(f"LLM 调用失败: {e}") from e
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 尝试截取首个 { 到末个 }
        a, b = text.find("{"), text.rfind("}")
        if a >= 0 and b > a:
            try:
                data = json.loads(text[a:b + 1])
            except json.JSONDecodeError:
                return {}
        else:
            return {}
    return data if isinstance(data, dict) else {}


def _report(on_status, pct: int, msg: str):
    """安全调用进度回调（供生成阶段及时反馈百分比与文案）"""
    if callable(on_status):
        try:
            on_status(int(pct), msg)
        except Exception:
            pass


def _skill_name_from_md(md: str) -> str:
    """从 SKILL.md 的 frontmatter 提取 name（兼容 agent_skills._parse_skill_md 的解析）"""
    s = (md or "").strip()
    if not s.startswith("---"):
        return ""
    end = s.find("\n---", 3)
    if end <= 0:
        return ""
    for line in s[3:end].splitlines():
        line = line.strip()
        if line.startswith("name:"):
            return line[len("name:"):].strip().strip("\"'")
    return ""


def _skill_dir_name(name: str) -> str:
    """技能目录名：去路径穿越字符、空安全，空格转下划线"""
    n = (name or "").strip().strip("\"'")
    n = _re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", n).strip()
    n = n.replace(" ", "_")
    if not n or n in (".", ".."):
        return ""
    return n


def _split_skill_docs(md: str) -> list:
    """把含一个或多个 SKILL.md 的文本按顶格 --- 文档分隔符拆成单个文档"""
    docs, cur, in_front = [], [], False
    for line in (md or "").splitlines():
        if line.strip() == "---":
            if in_front:
                cur.append(line)
                in_front = False
            else:
                if cur and any(l.strip() for l in cur):
                    docs.append("\n".join(cur))
                    cur = []
                cur.append(line)
                in_front = True
            continue
        cur.append(line)
    if cur and any(l.strip() for l in cur):
        docs.append("\n".join(cur))
    return docs


def _write_workflow_skills(d: Path, raw) -> int:
    """把 AI 生成的工作流技能内容写入 <工作流>/skills/<技能名>/SKILL.md 并配置。

    raw 支持：dict{技能名: SKILL.md} / 单个 SKILL.md 字符串 / 多个用 --- 分隔的字符串 / 列表。
    写入后失效技能缓存，技能即时合并进技能库（随该工作流生效）。
    返回写入的技能数。"""
    if raw is None:
        return 0
    sdir = d / "skills"
    entries = []
    if isinstance(raw, dict):
        for sname, md in raw.items():
            md = (md or "").strip()
            if md:
                entries.append((_skill_dir_name(str(sname)), md))
    elif isinstance(raw, list):
        for md in raw:
            md = (md or "").strip()
            if md:
                entries.append(("", md))
    elif isinstance(raw, str) and raw.strip():
        for doc in _split_skill_docs(raw):
            doc = doc.strip()
            if doc:
                entries.append(("", doc))
    written = []
    for sname, md in entries:
        name = sname or _skill_dir_name(_skill_name_from_md(md))
        if not name:
            continue
        sd = sdir / name
        try:
            sd.mkdir(parents=True, exist_ok=True)
            (sd / "SKILL.md").write_text(md.rstrip() + "\n", encoding="utf-8")
            written.append(name)
        except OSError:
            continue
    if written:
        try:
            from zhuzhu_Copilot.core import agent_skills
            agent_skills.invalidate_skills_cache()   # 新技能立即加载生效
        except Exception:
            pass
    return len(written)


def create_workflow_from_nl(description: str, files: list = None, on_status=None) -> tuple:
    """用自然语言描述创建 Agent 工作流（真实 AI 生成核心文件，缺失文件用模板兜底）。
    返回 (ok, message)。on_status 可选回调，用于生成阶段及时反馈进度。"""
    desc = (description or "").strip()
    want = [f for f in (files or []) if f in CORE_FILES] or list(CORE_FILES)
    if desc:
        _report(on_status, 5, f"正在分析需求并设计工作流（{', '.join(want)}）…")
    else:
        return False, "请用自然语言描述你想要的 Agent 工作流"
    try:
        spec = _ai_generate_workflow(desc, want)
    except Exception as e:
        return False, f"AI 生成失败: {e}"
    if not spec:
        return False, "AI 未返回有效的工作流设计，请换一种描述重试"
    _report(on_status, 80, "AI 已完成设计，正在写入核心文件…")
    name = _safe_name(str(spec.get("name") or ""))
    if not name:
        return False, "AI 返回的工作流名不合法（仅字母/数字/下划线/中划线，且不能为 _default）"
    if workflow_dir(name).exists():
        return False, f"工作流「{name}」已存在，请换名或删除后重试"
    gen_files = spec.get("files") or {}
    tpl = _template_dir()
    n_skill = 0
    try:
        d = workflow_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        for f in want:
            if f == "skills":
                # 技能目录先按模板兜底，AI 生成的技能随后覆盖/追加写入
                src = tpl / f
                dst = d / f
                if src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    dst.mkdir(parents=True, exist_ok=True)
                _report(on_status, 80 + int(15 * (want.index(f) + 1) / len(want)),
                        "正在写入核心文件… （skills）")
                continue
            content = gen_files.get(f)
            if isinstance(content, str) and content.strip() and f != "plugins":
                (d / f).write_text(content, encoding="utf-8")
            else:
                src = tpl / f
                dst = d / f
                if src.is_file():
                    shutil.copy2(src, dst)
                elif src.is_dir():
                    shutil.copytree(src, dst)
                else:
                    dst.mkdir(parents=True, exist_ok=True)
            _report(on_status, 80 + int(15 * (want.index(f) + 1) / len(want)),
                    f"正在写入核心文件… （{f}）")
        # 自动生成并配置工作流技能：写入 skills/<技能名>/SKILL.md，即时合并进技能库
        n_skill = _write_workflow_skills(d, gen_files.get("skills"))
        if n_skill:
            _report(on_status, 97, f"正在配置技能（自动生成 {n_skill} 个技能）…")
        _write_meta(name, {
            "name": name,
            "description": str(spec.get("description") or desc),
            "enabled": True,
            "core_files": want,
            "created": time.strftime("%Y-%m-%d %H:%M"),
            "updated": time.strftime("%Y-%m-%d %H:%M"),
        })
        _report(on_status, 98, "保存元数据完成…")
        invalidate_workflow_caches()   # 新建的核心文件/技能立即可被加载
        if n_skill:
            return True, (f"已用 AI 创建工作流「{name}」，并自动生成配置 {n_skill} 个技能"
                          f"（核心文件: {', '.join(want)}）")
        return True, f"已用 AI 创建工作流「{name}」（核心文件: {', '.join(want)}）"
    except OSError as e:
        try:
            shutil.rmtree(workflow_dir(name))
        except OSError:
            pass
        return False, f"创建工作流失败: {e}"
