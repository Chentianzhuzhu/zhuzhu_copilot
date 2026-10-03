"""插件系统：统一管理 MCP 连接与标准技能（SKILL.md）两种扩展。

插件 = 一个目录 ~/.zhuzhu_Copilot/plugins/<name>/：
  plugin.json     插件元数据（名称/描述/类型/启用状态/构成 + 自检结果 verified/verify_detail）
  SKILL.md        标准技能文件（skill / combined / web 型插件）
  server.py       本地服务脚本：stdio MCP server（mcp/combined 型）；web 型同文件兼作
                  本地 HTTP Server（浏览器 UI + /api/*），并内置 `--selftest` 离线自检入口
  deploy.py       远程 SSE 部署脚本（mcp / combined 型插件，可选）
  requirements.txt  Python 依赖（可选）
  examples/       示例资源文件（可选）

插件类型 kind：
  mcp      仅提供 MCP 工具（本地 stdio 或远程 SSE）
  skill    仅提供标准技能 SKILL.md
  combined 同时提供技能与 MCP 工具
  web      三件套：SKILL.md 技能 + MCP 工具 + 本地 HTTP 浏览器界面（可视化交互类）

生成链路（`create_plugin_from_nl`）：AI 设计 → 落盘 → **冒烟自检**（py_compile → web 打
HTTP 端点 / mcp 走 stdio tools/list）→ 未过则把错误回灌给模型自修（有界轮数）→ 通过才登记
MCP（避免坏 server 进注册表，每次会话都拉起报错）。

MCP 传输由 plugin.json 声明（可组合「远程 MCP server + 本地 SKILL.md 技能」的市场插件）：
  mcp_type = stdio（默认，命令跑 server.py / deploy.py）| sse（远程，需 mcp_url）
  mcp_url    远程 SSE 端点（mcp_type=sse 时必填，无需 server.py）
  mcp_command / mcp_args  stdio 命令与参数覆盖（缺省用内置解释器跑 server.py）

导入插件：zip 包（含 plugin.json）。导入成功后自动登记技能与 MCP，即插即用。
"""

from zhuzhu_Copilot import app_identity
import json
import os
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path

from zhuzhu_Copilot.core import agent_runtime, agent_skills

PLUGINS_DIR = app_identity.data_root() / "plugins"

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")


def _shipped_plugins_dir() -> Path:
    """随应用分发的内置插件资源目录：
    - 开发模式：src/zhuzhu_Copilot/plugins
    - 打包（onedir/onefile）：依次尝试 _MEIPASS/plugins、<exe>/_internal/plugins、<exe>/plugins"""
    if getattr(sys, "frozen", False):
        for base in (getattr(sys, "_MEIPASS", None),
                     str(Path(sys.executable).parent / "_internal"),
                     str(Path(sys.executable).parent)):
            if not base:
                continue
            cand = Path(base) / "plugins"
            if cand.is_dir():
                return cand
        return Path()
    return Path(__file__).resolve().parent.parent / "plugins"


def ensure_shipped_plugins() -> None:
    """随包分发插件：用户插件目录缺失同名插件时整体复制（含 plugin.json/SKILL.md/server.py/examples）。
    已存在的同名插件不覆盖，保持一致策略；复制完成后登记 MCP 与技能（combined 型）。"""
    root = plugins_dir()
    shipped = _shipped_plugins_dir()
    if not shipped.is_dir():
        return
    for d in shipped.iterdir():
        if not d.is_dir() or not (d / "plugin.json").is_file():
            continue
        name = d.name
        target = root / name
        if target.is_dir():
            continue
        try:
            shutil.copytree(d, target)
        except OSError:
            continue
        _register_shipped_plugin(name)
        invalidate_index()   # 新落盘插件要立刻出现在来源索引里（否则升级后本会话看不到它）


def _register_shipped_plugin(name: str) -> None:
    """随包插件首次落盘后的登记：校验已启用的 description，并联动登记其 MCP/技能。"""
    meta = _read_meta(name)
    if not meta:
        return
    try:
        servers = agent_skills.load_mcp_servers()
        bound = meta.get("mcp_name")
        if bound and not any(s.get("name") == bound for s in servers):
            servers.append(_mcp_config_for(meta))
            agent_skills.save_mcp_servers(servers)
    except Exception:
        pass
    try:
        skill = (plugin_dir(name) / "SKILL.md")
        if skill.is_file() and meta.get("skill_name"):
            md = skill.read_text(encoding="utf-8", errors="replace")
            s = _parse_skill_md_local(md)
            if s and s.get("instruction"):
                agent_skills.create_md_skill(meta["skill_name"],
                                             s.get("description") or meta.get("description") or name,
                                             s.get("instruction"))
    except Exception:
        pass


def _parse_skill_md_local(text: str) -> dict:
    """轻量解析 SKILL.md frontmatter（name/description）与正文，供随包插件登记技能。"""
    s = (text or "").strip()
    if not s:
        return {}
    name = desc = ""
    body = s
    if s.startswith("---"):
        end = s.find("\n---", 3)
        if end > 0:
            fm = s[3:end].strip()
            body = s[end + 4:].strip()
            for line in fm.splitlines():
                line = line.strip()
                if line.startswith("name:"):
                    name = line[len("name:"):].strip().strip("\"'")
                elif line.startswith("description:"):
                    desc = line[len("description:"):].strip().strip("\"'")
    return {"name": name, "description": desc, "instruction": body}


def plugins_dir() -> Path:
    """统一插件根目录（不存在则创建）"""
    try:
        PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return PLUGINS_DIR


def plugin_dir(name: str) -> Path:
    return plugins_dir() / name


def _read_meta(name: str) -> dict:
    d = plugin_dir(name)
    f = d / "plugin.json"
    if not f.is_file():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_meta(name: str, meta: dict) -> bool:
    d = plugin_dir(name)
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / "plugin.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        invalidate_index()      # 插件构成变化 → 来源索引（工具/技能归属）随之失效
        return True
    except OSError:
        return False


def list_plugins() -> list:
    """扫描插件目录，返回插件元数据列表（含默认字段补齐）"""
    ensure_shipped_plugins()   # 首启随包插件同步（已存在则快速跳过）
    root = plugins_dir()
    out = []
    try:
        names = sorted(p.name for p in root.iterdir() if p.is_dir())
    except OSError:
        names = []
    for n in names:
        meta = _read_meta(n)
        meta.setdefault("name", n)
        meta.setdefault("description", "")
        meta.setdefault("kind", "mcp")
        meta.setdefault("enabled", True)
        meta.setdefault("created_at", "")
        # 推断构成：目录内存在 SKILL.md / server.py / deploy.py，或声明远程 SSE
        d = plugin_dir(n)
        has_skill = (d / "SKILL.md").is_file()
        has_mcp = ((d / "server.py").is_file() or (d / "deploy.py").is_file()
                   or str(meta.get("mcp_type") or "").lower() == "sse")
        meta["has_skill"] = has_skill
        meta["has_mcp"] = has_mcp
        out.append(meta)
    return out


def plugin_mcp_names() -> set:
    """所有已安装插件登记的 MCP 服务器名（{name}-mcp）。供插件能力开关裁剪。"""
    return {f"{p.get('name')}-mcp" for p in list_plugins() if p.get("name")}


def plugin_skill_names() -> set:
    """所有已安装插件对应的技能名（插件目录名作为技能名登记）。供插件能力开关裁剪。"""
    return {p.get("name") for p in list_plugins() if p.get("name")}


def get_plugin(name: str) -> dict:
    for p in list_plugins():
        if p.get("name") == name:
            return p
    return {}


# ---------------------------------------------------------------------------
# 插件来源索引：插件登记的 MCP 工具 / 技能 → 归属插件
#
# 用途（UI 与提示词都要用，故必须便宜）：聊天流里给「来自插件」的工具行/技能行换插件专属
# 矢量图标并给出气泡提示；手动调用插件时按插件名取回说明与调用规范。
# list_plugins() 每次都扫目录，逐次调用会拖慢流式渲染，因此这里做一份按需缓存，
# 任何写操作（新增/导入/启停/删除）都会失效它。
# ---------------------------------------------------------------------------
MCP_SUFFIX = "-mcp"          # 插件登记的 MCP 服务器名固定为「{插件名}-mcp」
_INDEX_CACHE: dict = {}


def invalidate_index() -> None:
    """插件构成变化后失效来源索引（所有写操作都会调用）"""
    _INDEX_CACHE.clear()


def plugin_index() -> dict:
    """插件来源索引（缓存）：
      plugins: {插件名: 元数据}
      servers: {MCP 服务器名: 插件名}
      skills:  {技能名: 插件名}
    """
    if _INDEX_CACHE:
        return _INDEX_CACHE
    plugins, servers, skills = {}, {}, {}
    try:
        for p in list_plugins():
            name = str(p.get("name") or "")
            if not name:
                continue
            plugins[name] = p
            servers[f"{name}{MCP_SUFFIX}"] = name
            if p.get("has_skill") or p.get("skill_name"):
                skills[str(p.get("skill_name") or name)] = name
    except Exception:
        pass
    _INDEX_CACHE.update({"plugins": plugins, "servers": servers, "skills": skills})
    return _INDEX_CACHE


def plugin_of_server(server: str) -> str:
    """MCP 服务器名 → 归属插件名（非插件服务器返回空串）"""
    return plugin_index()["servers"].get(str(server or ""), "")


def plugin_of_skill(skill: str) -> str:
    """技能名 → 归属插件名（插件目录名即其技能名；非插件技能返回空串）"""
    return plugin_index()["skills"].get(str(skill or ""), "")


def plugin_names() -> list:
    """已安装插件名清单（按名称排序）"""
    return sorted(plugin_index()["plugins"])


def list_plugin_calls() -> list:
    """供输入框斜杠候选：可手动调用的插件 [{name, description}]（停用的插件不可调用）"""
    out = []
    for name in plugin_names():
        meta = plugin_index()["plugins"].get(name) or {}
        if not meta.get("enabled", True):
            continue
        out.append({"name": name, "description": str(meta.get("description") or "")})
    return out


def filter_enabled_plugins(names: list) -> list:
    """过滤出真实存在且已启用的插件名（去重、保持入参顺序）"""
    idx = plugin_index()["plugins"]
    out = []
    for raw in names or []:
        n = str(raw or "").strip()
        if n in idx and idx[n].get("enabled", True) and n not in out:
            out.append(n)
    return out


def plugin_skill_names_of(names: list) -> list:
    """这些插件登记的技能名（引擎据此把技能并入本任务技能集 → 覆盖的工具走硬拦截）"""
    idx = plugin_index()["plugins"]
    out = []
    for raw in names or []:
        skill = str((idx.get(str(raw or "").strip()) or {}).get("skill_name") or "").strip()
        if skill and skill not in out:
            out.append(skill)
    return out


def plugin_skill_md(name: str) -> str:
    """插件自带 SKILL.md 正文（无则空串）"""
    try:
        f = plugin_dir(name) / "SKILL.md"
        if f.is_file():
            return f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    return ""


def plugin_spec_text(names: list) -> str:
    """手动调用插件时注入模型上下文的完整规范文本。

    插件对模型而言是「黑盒能力」，只说名字没有意义 —— 必须把**插件说明、它提供的
    MCP 工具、其 SKILL.md 规范、调用方式**一并给出，模型才知道该调哪个工具、按什么
    流程走。任一节缺失都会退化为「猜」，故这里逐段拼接，缺项自动省略。
    """
    idx = plugin_index()["plugins"]
    blocks = []
    for raw in names or []:
        name = str(raw or "").strip()
        meta = idx.get(name)
        if not meta:
            continue
        parts = [f"### 插件 {name}"]
        desc = str(meta.get("description") or "").strip()
        if desc:
            parts.append(f"用途说明：{desc}")
        mcp_name = str(meta.get("mcp_name") or f"{name}{MCP_SUFFIX}")
        if meta.get("has_mcp"):
            parts.append(
                f"它提供的 MCP 工具已注册为服务器「{mcp_name}」的工具，"
                f"按工具清单中的 function 直接调用即可；这些工具是该插件的唯一执行入口。")
        if meta.get("has_skill"):
            parts.append(f"它登记的技能名为「{meta.get('skill_name') or name}」，"
                         f"技能规范（SKILL.md）如下：")
        body = plugin_skill_md(name).strip()
        if body:
            parts.append(body)
        parts.append("调用规范：先按上述说明与流程确定要调用的工具，再直接调用对应工具；"
                     "不得用其他工具替代、不得跳过流程，也不得只描述而不真正调用。")
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)


def plugin_workflows(name: str) -> list:
    """插件绑定的工作流列表（空 = 未绑定/全局）"""
    return agent_skills.get_workflow_binding("plugin", name or "")


def set_plugin_workflows(name: str, workflows: list) -> tuple:
    """指定插件可用的一个或多个工作流（空列表 = 解除绑定回全局）。
    插件登记的技能（skill_name）与 MCP 服务器（mcp_name）同步绑定，运行时按其生效。"""
    name = (name or "").strip()
    if not name:
        return False, "插件名不能为空"
    meta = _read_meta(name)
    if not meta:
        return False, f"插件「{name}」不存在"
    # 校验工作流存在，并传播绑定到插件 + 其技能 + 其 MCP 服务器
    ok, msg = agent_skills.set_workflow_binding("plugin", name, workflows)
    if not ok:
        return False, msg
    for kind, res in (("skill", meta.get("skill_name")), ("mcp", meta.get("mcp_name"))):
        if res:
            try:
                agent_skills.set_workflow_binding(kind, res, workflows)
            except Exception:
                pass
    return True, (f"已指定插件「{name}」用于工作流: {', '.join(workflows)}"
                  if workflows else f"已解除插件「{name}」工作流绑定（恢复全局）")


def _mcp_config_for(meta: dict) -> dict:
    """根据插件元数据重建 MCP 服务器配置。

    - mcp_type=sse → 远程 SSE（{name, type:sse, url}），需 mcp_url，无需 server.py
    - 默认/stdio → 本地 stdio：优先 mcp_command/mcp_args，缺省用内置解释器跑 server.py
    声明 sse 但缺 mcp_url（且无 server.py 可回退）时返回 None，调用方跳过登记。"""
    name = meta.get("name", "")
    d = plugin_dir(name)
    mcp_name = meta.get("mcp_name") or f"{name}-mcp"
    typ = str(meta.get("mcp_type") or "stdio").strip().lower()
    if typ == "sse":
        url = str(meta.get("mcp_url") or "").strip()
        if url:
            return {"name": mcp_name, "type": "sse", "url": url}
        if not (d / "server.py").is_file():
            return None
        typ = "stdio"   # 有本地脚本时回退 stdio
    command = str(meta.get("mcp_command") or "").strip()
    if command:
        args = [str(x) for x in (meta.get("mcp_args") or [])]
    else:
        command = agent_runtime.python_interpreter()
        args = [str(d / "server.py")]
    return {"name": mcp_name, "type": "stdio", "command": command, "args": args}


def set_plugin_enabled(name: str, enabled: bool) -> tuple:
    """启用/停用插件：停用时不删除文件，仅标记 enabled=False，并从 mcp_servers.json
    移除/恢复对应 MCP 服务器配置（联动生效）。返回 (ok, message)。"""
    meta = _read_meta(name)
    if not meta:
        return False, f"插件「{name}」不存在"
    meta["enabled"] = bool(enabled)
    ok = _write_meta(name, meta)
    if not ok:
        return False, "写入插件配置失败"
    # 联动 MCP：停用移除登记，启用恢复登记
    try:
        servers = agent_skills.load_mcp_servers()
        bound = meta.get("mcp_name")
        if bound:
            if enabled:
                cfg = _mcp_config_for(meta)
                if cfg and not any(s.get("name") == bound for s in servers):
                    servers.append(cfg)
            else:
                servers = [s for s in servers if s.get("name") != bound]
            agent_skills.save_mcp_servers(servers)
    except Exception:
        pass
    return True, f"已{'启用' if enabled else '停用'}插件「{name}」"


def delete_plugin(name: str) -> tuple:
    """删除插件：移除插件目录；若它登记了 MCP 服务器或技能则一并解绑。"""
    if not _NAME_RE.match(name or ""):
        return False, "插件名不合法"
    d = plugin_dir(name)
    if not d.is_dir():
        return False, f"插件「{name}」不存在"
    meta = _read_meta(name)
    # 1. 解绑已登记的 MCP 服务器（mcp_servers.json）
    try:
        bound = meta.get("mcp_name")
        if bound:
            servers = agent_skills.load_mcp_servers()
            servers = [s for s in servers if s.get("name") != bound]
            agent_skills.save_mcp_servers(servers)
    except Exception:
        pass
    # 2. 解绑已登记的技能（删除技能目录）
    try:
        skill = meta.get("skill_name")
        if skill:
            agent_skills.delete_skill(skill)
    except Exception:
        pass
    # 2.5 清理工作流绑定（插件本体 + 其技能 + 其 MCP 服务器）
    agent_skills.remove_workflow_binding("plugin", name)
    for kind, res in (("skill", meta.get("skill_name")), ("mcp", meta.get("mcp_name"))):
        if res:
            try:
                agent_skills.remove_workflow_binding(kind, res)
            except Exception:
                pass
    # 3. 删除插件目录
    try:
        shutil.rmtree(d)
    except OSError as e:
        return False, f"删除插件目录失败: {e}"
    invalidate_index()
    return True, f"已删除插件「{name}」"


# ---------- 导入 ----------

def import_plugin_zip(zip_path: str) -> tuple:
    """导入插件 zip 包：包内应含 plugin.json（或直接为 <name>/plugin.json 结构）。
    防路径穿越；导入后自动登记技能（SKILL.md → 技能目录）与 MCP
    （本地 stdio server.py / 远程 SSE mcp_url → mcp_servers.json），即插即用。
    返回 (ok, message)。"""
    p = Path(zip_path or "")
    if not p.is_file():
        return False, "文件不存在"
    root = plugins_dir()
    try:
        with zipfile.ZipFile(p) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            metas = [i for i in infos
                     if i.filename.replace("\\", "/").endswith("plugin.json")]
            if not metas:
                return False, "压缩包内未找到 plugin.json"
            # 取第一个 plugin.json 定位插件名；确定需要剥离的顶层前缀
            rel = metas[0].filename.replace("\\", "/")
            parent_dir = "/".join([x for x in rel.split("/") if x][:-1])   # plugin.json 所在目录
            try:
                meta0 = json.loads(z.read(metas[0]))
            except Exception:
                meta0 = {}
            meta_name = str((meta0 or {}).get("name") or "").strip()
            if parent_dir:
                # 插件根 = plugin.json 所在目录的末段；剥离该前缀
                segs = [x for x in parent_dir.split("/") if x]
                name = meta_name or segs[-1]
                strip_prefix = parent_dir + "/"
            else:
                # plugin.json 在包根 → 从元数据读取 name，无需剥离
                name = meta_name
                strip_prefix = ""
            if not _NAME_RE.match(name or ""):
                return False, "插件名仅支持字母/数字/下划线/连字符（≤50 字符）"
            target = root / name
            if target.exists():
                return False, f"插件「{name}」已存在"
            for info in infos:
                relpath = info.filename.replace("\\", "/")
                if relpath.startswith("/") or ".." in relpath.split("/"):
                    return False, "压缩包内含非法路径（拒绝导入）"
                if strip_prefix and relpath.startswith(strip_prefix):
                    relpath = relpath[len(strip_prefix):]
                dest = (target / relpath).resolve()
                if not str(dest).startswith(str(target.resolve())):
                    return False, "压缩包内含越界路径（拒绝导入）"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(info))
        msg = f"已导入插件「{name}」（{target}）"
        reg_ok, reg_msg = _register_imported_plugin(name)
        if reg_msg:
            msg += f"；{reg_msg}"
        return reg_ok, msg
    except zipfile.BadZipFile:
        return False, "不是有效的 zip 包"
    except OSError as e:
        return False, f"导入失败: {e}"


def _register_imported_plugin(name: str) -> tuple:
    """导入 zip 后的自动登记：SKILL.md → 技能目录（市场标准 md）；
    MCP（本地 stdio server.py / 远程 SSE mcp_url）→ mcp_servers.json。
    使市场插件（远程 MCP server + 本地技能组合）导入后立即可用。返回 (ok, message)。"""
    meta = _read_meta(name)
    if not meta:
        return False, "插件缺少 plugin.json"
    parts = []
    # 1. 登记技能：SKILL.md 复制到技能目录
    skill_path = plugin_dir(name) / "SKILL.md"
    if skill_path.is_file():
        try:
            s = _parse_skill_md_local(skill_path.read_text(encoding="utf-8", errors="replace"))
            if s and s.get("instruction"):
                skill_name = str(meta.get("skill_name") or s.get("name") or name).strip()
                ok, msg = agent_skills.create_md_skill(
                    skill_name,
                    s.get("description") or meta.get("description") or name,
                    s.get("instruction"))
                if ok:
                    meta.setdefault("skill_name", skill_name)
                    parts.append(f"技能 /{skill_name} 已登记")
                else:
                    parts.append(f"技能登记失败: {msg}")
        except Exception:
            parts.append("技能登记异常")
    # 2. 登记 MCP：本地 stdio（server.py）或远程 SSE（mcp_type=sse + mcp_url）
    has_server = (plugin_dir(name) / "server.py").is_file() or (plugin_dir(name) / "deploy.py").is_file()
    has_sse = (str(meta.get("mcp_type") or "").lower() == "sse"
               and str(meta.get("mcp_url") or "").strip())
    if has_server or has_sse:
        cfg = _mcp_config_for(meta)
        if cfg:
            meta.setdefault("mcp_name", cfg["name"])
            try:
                servers = agent_skills.load_mcp_servers()
                if not any(s.get("name") == cfg["name"] for s in servers):
                    servers.append(cfg)
                    agent_skills.save_mcp_servers(servers)
                parts.append(f"MCP {cfg['name']}（{cfg.get('type')}）已登记")
            except Exception:
                parts.append("MCP 登记异常")
        else:
            parts.append("MCP 登记跳过（远程 sse 缺少 mcp_url）")
    _write_meta(name, meta)
    return True, "；".join(parts)


def import_plugin_skill(skill_path: str) -> tuple:
    """导入标准技能为 skill 型插件：将 SKILL.md（或含 SKILL.md 的 zip）包装为插件。"""
    p = Path(skill_path or "")
    if not p.is_file():
        return False, "文件不存在"
    try:
        # 先借用 agent_skills.import_skill_file 把 SKILL.md（或含 SKILL.md 的 zip）导入技能目录，
        # 技能名取 frontmatter（或文件名）；再把技能目录包装为 skill 型插件
        ok, msg = agent_skills.import_skill_file(str(p))
        if not ok:
            return False, msg
        name = msg.split("「")[-1].split("」")[0] if "「" in msg else p.stem
        skill_src = agent_skills._skills_dir() / name
        return _wrap_skill_dir(name, skill_src)
    except Exception as e:
        return False, f"导入失败: {e}"


def _wrap_skill_dir(name: str, skill_src: Path) -> tuple:
    """把技能目录包装为 skill 型插件：复制到插件目录并登记。"""
    if not _NAME_RE.match(name or ""):
        return False, "插件名仅支持字母/数字/下划线/连字符（≤50 字符）"
    if not skill_src.is_dir():
        return False, f"技能「{name}」不存在"
    if plugin_dir(name).exists():
        return False, f"插件「{name}」已存在"
    try:
        shutil.copytree(skill_src, plugin_dir(name), dirs_exist_ok=True)
    except OSError as e:
        return False, f"复制技能失败: {e}"
    meta = {"name": name, "kind": "skill", "enabled": True,
            "skill_name": name, "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "description": _read_skill_desc(plugin_dir(name) / "SKILL.md")}
    _write_meta(name, meta)
    return True, f"已导入技能插件「{name}」（skill 型）"


def _read_skill_desc(md_path: Path) -> str:
    try:
        txt = md_path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^description:\s*(.+)$", txt, re.M)
        return (m.group(1).strip().strip('"') if m else "") or ""
    except Exception:
        return ""


# ---------- 工具：AI 生成插件 ----------

def create_plugin_from_nl(description: str, kind: str = "combined", on_status=None) -> tuple:
    """用自然语言描述创建插件（真实 AI 生成）：生成 SKILL.md + MCP server 脚本，
    并自动登记到技能目录与 mcp_servers.json。返回 (ok, message)。
    on_status 可选回调，用于生成阶段及时反馈 (百分比, 文案)。"""
    def rep(pct, msg):
        if callable(on_status):
            try:
                on_status(int(pct), msg)
            except Exception:
                pass

    desc = (description or "").strip()
    if not desc:
        return False, "请用自然语言描述你想要的插件功能"
    kind = (kind or "combined").strip().lower()
    if kind not in ("mcp", "skill", "combined", "web"):
        kind = "combined"
    rep(5, "正在分析需求并生成插件设计…")
    try:
        spec = _ai_generate_spec(desc, kind)
    except Exception as e:
        return False, f"AI 生成失败: {e}"
    if not spec:
        return False, "AI 未返回有效插件设计"
    name = str(spec.get("name") or "").strip().lower()
    if not _NAME_RE.match(name or ""):
        return False, "AI 返回的插件名不合法，请换一种描述重试"
    if plugin_dir(name).exists():
        return False, f"插件「{name}」已存在，请换名或删除后重试"

    rep(60, "AI 已完成设计，正在写入插件文件…")
    ok, err = _write_plugin_files(name, kind, spec)
    rep(85, "正在冒烟自检（语法 / HTTP 端点 / MCP tools）…")
    verified, detail = _verify_plugin(name, kind) if ok else (False, err)
    # 自检未过 → 把错误回灌给模型让它自己修（有界次数，避免生成时长失控）
    repairs = 0
    while not verified and repairs < _VERIFY_MAX_REPAIRS:
        repairs += 1
        rep(85 + repairs * 3, f"自检未通过，正在让 AI 修正（第 {repairs} 次）…")
        try:
            fixed = _ai_generate_spec(_repair_desc(desc, kind, detail), kind)
        except Exception as e:
            detail = f"{detail}；修正轮 AI 调用失败: {e}"
            break
        if not fixed:
            detail = f"{detail}；修正轮 AI 未返回有效设计"
            break
        # 修正轮一律写回**原插件目录**：目录名/登记名已定，不允许改名
        ok2, err2 = _write_plugin_files(name, kind, fixed)
        if not ok2:
            detail = err2
            break
        spec = fixed
        verified, detail = _verify_plugin(name, kind)

    rep(96, "正在登记技能与 MCP 配置…")
    d = plugin_dir(name)
    parts = _spec_parts(spec, kind)
    has_skill, has_mcp = parts["has_skill"], parts["has_mcp"]
    try:
        meta = {"name": name, "kind": kind, "enabled": True,
                "description": parts["summary"] or desc,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "verified": bool(verified), "verify_detail": detail}
        # 登记 MCP：本地 server.py → stdio（本地部署）。
        # **只在自检通过时登记**：坏 server 一旦进注册表，每次会话都会拉起并报错刷屏。
        if has_mcp and verified:
            servers = agent_skills.load_mcp_servers()
            mcp_name = f"{name}-mcp"
            meta["mcp_name"] = mcp_name
            entry = {"name": mcp_name, "type": "stdio",
                     "command": agent_runtime.python_interpreter(),
                     "args": [str(d / "server.py")]}
            servers[:] = [s for s in servers if s.get("name") != mcp_name]   # 幂等：同名覆盖
            servers.append(entry)
            agent_skills.save_mcp_servers(servers)
        # 登记技能：SKILL.md 复制到技能目录（技能是纯文档，不受 server 自检结果影响）
        if has_skill:
            ok_s, _msg = agent_skills.create_md_skill(
                name, parts["summary"], parts["skill_md"])
            if ok_s:
                meta["skill_name"] = name
        _write_meta(name, meta)
    except OSError as e:
        return False, f"创建插件失败: {e}"

    lines = [f"已创建插件「{name}」（{kind} 型）", f"描述: {parts['summary'] or desc}"]
    if has_mcp and verified:
        lines.append(f"MCP: 本地 stdio {d / 'server.py'}（已登记并即时生效）")
    if has_skill:
        lines.append(f"技能: /{name} 或对话描述即可调用")
    if kind == "web":
        lines.append("浏览器界面: 先调用 web_url 工具获取访问地址，"
                     "再用 browser_open 打开给用户（浏览器与 AI 共用同一份状态）。"
                     "操作者必须是用户本人，严禁 AI 自我演示或与脚本 AI 自动对战")
        lines.append("自检可用 web_fetch(url=\"http://127.0.0.1:<端口>/\") 复验页面与 "
                     "/api/<端点>（本地回环地址已放开，可直接抓取）。")
    if verified:
        lines.append(f"✅ 冒烟自检通过：{detail}")
    else:
        lines.append("⚠️ 冒烟自检未通过，本次**未登记 MCP**（避免坏服务器进注册表）："
                     f"{detail}")
        lines.append(f"插件文件在 {d}，可用 read_file/write_file 修正后重试；"
                     "或重新调用 create_plugin 换一种描述。")
    return True, "\n".join(lines)


def _spec_parts(spec: dict, kind: str) -> dict:
    """从 AI 返回的设计 JSON 提取各产物（web 型与 mcp 型的字段名不同，统一在这里归一）。"""
    skill_md = str(spec.get("skill_md") or "")
    api = spec.get("api") or []
    web_page = str(spec.get("web_page") or "").strip()
    tools = spec.get("tools") or []
    if kind == "web":
        # web 型：MCP 工具由 API 端点自动映射（api 与 web_page 缺一不可）
        has_mcp = bool(api) and bool(web_page)
    else:
        has_mcp = (kind in ("mcp", "combined")) and bool(tools)
    return {"summary": spec.get("summary") or "", "skill_md": skill_md,
            "tools": tools, "api": api, "web_page": web_page,
            "deps": [str(x).strip() for x in (spec.get("dependencies") or [])
                     if str(x).strip()],
            "has_skill": bool(skill_md.strip()), "has_mcp": has_mcp}


def _write_plugin_files(name: str, kind: str, spec: dict) -> tuple:
    """把设计写成插件文件（skill/web/mcp 三件套按类型落盘）。返回 (ok, 说明/错误)。

    缺必需产物时**不落盘**并返回可读原因 —— 该原因会回灌给模型触发自修
    （见 create_plugin_from_nl 的修正轮），比写出一个空 server.py 更有价值。
    """
    parts = _spec_parts(spec, kind)
    if kind == "web":
        if not parts["web_page"]:
            return False, "web 型缺少 web_page（单页 HTML），无法生成浏览器界面"
        if not parts["api"]:
            return False, "web 型缺少 api 端点（需 2-6 个），浏览器与 MCP 工具都依赖它"
    if kind in ("mcp", "combined") and not parts["tools"]:
        return False, f"{kind} 型缺少 tools（需 1-5 个可调用工具）"
    if kind == "skill" and not parts["has_skill"]:
        return False, "skill 型缺少 skill_md"
    try:
        d = plugin_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        if parts["has_skill"]:
            (d / "SKILL.md").write_text(parts["skill_md"], encoding="utf-8")
        if parts["has_mcp"]:
            src = (_assemble_web_server_py(name, parts["web_page"], parts["api"])
                   if kind == "web" else _assemble_server_py(name, parts["tools"]))
            if not src:
                return False, "server.py 组装结果为空（api/tools 结构不完整）"
            (d / "server.py").write_text(src, encoding="utf-8")
        if parts["deps"]:
            (d / "requirements.txt").write_text("\n".join(parts["deps"]), encoding="utf-8")
        examples = spec.get("examples") or {}
        if isinstance(examples, dict) and examples:
            ex = d / "examples"
            ex.mkdir(parents=True, exist_ok=True)
            for k, v in examples.items():
                (ex / str(k)).write_text(str(v), encoding="utf-8")
        return True, ""
    except OSError as e:
        return False, f"写入插件文件失败: {e}"


# ── 冒烟自检：语法 → 真跑（web 打 HTTP 端点；mcp/combined 走 stdio tools/list 握手） ──
_VERIFY_MAX_REPAIRS = 1          # 自修轮数上限（一轮足够修常见错误，再多会把生成时长拖长）
_VERIFY_TIMEOUT_S = 30.0         # 单个子进程自检时限（含解释器启动）


def _plugin_interpreter() -> str:
    """自检用解释器：与 MCP 登记一致（同一运行时，依赖可见）。"""
    try:
        return agent_runtime.python_interpreter()
    except Exception:
        return sys.executable


def _run_subprocess(cmd: list, stdin_text: str = "", cwd=None, timeout: float = None) -> tuple:
    """跑一次子进程并返回 (returncode, stdout, stderr)；超时/异常按失败处理。"""
    import subprocess
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"      # 生成物含中文，固定编码避免控制台编码干扰
    try:
        p = subprocess.run(cmd, input=stdin_text.encode("utf-8") if stdin_text else None,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           cwd=str(cwd) if cwd else None, env=env,
                           timeout=timeout or _VERIFY_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return -1, "", f"自检超时（>{int(timeout or _VERIFY_TIMEOUT_S)}s）"
    except Exception as e:
        return -1, "", str(e)
    return p.returncode, (p.stdout or b"").decode("utf-8", "replace"), \
        (p.stderr or b"").decode("utf-8", "replace")


def _mcp_handshake(server: Path) -> tuple:
    """向生成的 server.py 发 initialize + tools/list，校验工具清单。返回 (ok, 说明)。

    stdin 关闭即触发生成物的主循环退出（`for line in stdin` 读到 EOF），无需额外握手协议。
    """
    reqs = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
    ]) + "\n"
    rc, out, err = _run_subprocess([_plugin_interpreter(), str(server)], stdin_text=reqs,
                                   cwd=server.parent)
    if rc == -1:
        return False, err or "启动失败"
    tools, inited = None, False
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == 1:
            inited = bool(msg.get("result"))
        if msg.get("id") == 2:
            res = msg.get("result") or {}
            tools = res.get("tools")
    if not inited:
        return False, f"MCP initialize 无有效响应（stderr: {(err or '')[-300:] or '空'}）"
    if not tools:
        return False, "MCP tools/list 返回空工具集"
    bad = [t for t in tools if not t.get("name") or "inputSchema" not in t]
    if bad:
        return False, f"以下工具缺少 name/inputSchema: {[t.get('name') for t in bad]}"
    return True, f"MCP 工具 {len(tools)} 个：{', '.join(t['name'] for t in tools[:8])}"


def _verify_plugin(name: str, kind: str) -> tuple:
    """生成后冒烟校验，返回 (ok, 说明或错误)。

    三层（只要能跑就真跑，绝不只看文件存在）：
      1) 语法：`py_compile` 编译 server.py；
      2) web 型：`server.py --selftest` —— 起本地 HTTP，拉页面 + 逐个调 /api 端点；
      3) mcp/combined 型：stdio 发 initialize + tools/list，校验工具清单可解析。
    """
    d = plugin_dir(name)
    server = d / "server.py"
    if kind == "skill":
        md = d / "SKILL.md"
        if not md.is_file():
            return False, "缺少 SKILL.md"
        text = md.read_text(encoding="utf-8", errors="replace")
        if "name:" not in text or "description:" not in text:
            return False, "SKILL.md 缺少 frontmatter（需 name/description 字段）"
        return True, f"技能文档 {len(text)} 字符（skill 型无需 server）"
    if not server.is_file():
        return False, "缺少 server.py"
    # 先装依赖再自检：运行时会按 requirements.txt 自动装（见 agent_deps.ensure_dir_deps），
    # 自检若跳过这一步，凡带第三方依赖的插件都会因 ImportError 被误判为不合格。
    if (d / "requirements.txt").is_file():
        try:
            from zhuzhu_Copilot.core import agent_deps
            agent_deps.ensure_dir_deps(d)
        except Exception:
            pass          # 装依赖失败交给下面的自检报错（错误信息更具体）
    py = _plugin_interpreter()
    rc, _out, err = _run_subprocess([py, "-m", "py_compile", str(server)], cwd=d)
    if rc != 0:
        return False, f"server.py 语法编译失败：{(err or '').strip()[-500:]}"
    if kind == "web":
        rc, out, err = _run_subprocess([py, str(server), "--selftest"], cwd=d)
        data = {}
        for line in reversed((out or "").splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                break
        if not data:
            return False, (f"未拿到自检结果（退出码 {rc}）："
                           f"{(err or '').strip()[-400:] or 'stdout 为空'}")
        if not data.get("ok"):
            # 分开列「页面问题」与「端点问题」：修法完全不同，别让模型猜
            why = []
            if data.get("error"):
                why.append(str(data["error"]))
            eps = data.get("endpoints") or {}
            bad = {k: v for k, v in eps.items() if v != "ok"}
            if bad:
                why.append("端点失败 " + json.dumps(bad, ensure_ascii=False))
            return False, "；".join(why) or "自检未通过（未给出原因）"
        ok_mcp, mcp_msg = _mcp_handshake(server)
        if not ok_mcp:
            return False, mcp_msg
        return True, (f"页面 {data.get('page_bytes')} 字节、"
                      f"{len(data.get('endpoints') or {})} 个端点全部调通；{mcp_msg}")
    return _mcp_handshake(server)


def _repair_desc(desc: str, kind: str, detail: str) -> str:
    """构造「回灌错误让模型自修」的输入（沿用原需求 + 明确列出失败原因与修正要求）。"""
    return (f"{desc}\n\n"
            f"【上一次生成的插件（类型 {kind}）未通过自检，请修正后重新输出**完整** JSON】\n"
            f"自检错误：{detail}\n"
            f"修正要求：①保持同一插件名，不要改名；②只修有问题的部分，其余字段照原样重发；"
            f"③web 型必须给出完整单页 HTML（全部资源内联、无外网 CDN）与可用的 /api 端点实现；"
            f"④端点实现必须能从 args 取参、读写 _STATE、返回可读文本且不抛异常。")


def _ai_generate_spec(desc: str, kind: str) -> dict:
    """调用当前配置的 LLM 生成插件设计 JSON（真实 API）。

    AI 只负责设计工具定义与实现逻辑，MCP 协议骨架由 _assemble_server_py 组装，
    保证生成的 server.py 严格符合 agent_mcp 客户端约定的 JSON-RPC 协议。
    """
    from zhuzhu_Copilot.core import agent_llm
    cfg = agent_llm.load_model_config()
    client = agent_llm.LLMClient(cfg.get("base_url"), cfg.get("api_key"),
                                 cfg.get("model") or agent_llm.DEFAULT_MODEL)
    # 按插件类型给不同字段模板：web 型（本地 Server+浏览器 UI）提供 web_page+api；
    # mcp/combined 型提供 tools；skill 型提供 skill_md。
    common_rule = (
        "你是插件设计专家。根据用户自然语言描述，设计一个可运行的插件。"
        "**核心设计原则：优先提供 LLM 可以直接操控的工具**——能力拆成职责单一、可单独调用的"
        "工具（参数 schema 清晰、返回可读文本结果），由 LLM 通过工具组合完成整件事，"
        "严禁设计 LLM 无法直接操控的独立 GUI 程序。"
    )
    if kind == "web":
        sys_p = (
            common_rule +
            "本插件为 **web 型（三件套：SKILL.md + MCP 工具 + 本地 HTTP 浏览器界面）**："
            "为「需要图形化/可视化交互」的能力而生（如仪表盘、可视化面板、画布/表单类交互工具等）"
            "——AI 生成一个本地 HTTP 服务：用户在浏览器里直观操作/观看，LLM 通过同名 MCP 工具"
            "操控同一份共享状态。\n"
            "**硬性要求：①插件创建后，AI 必须立即调用 web_url 获取地址并用 browser_open 在浏览器里"
            "打开界面给用户；②图形化界面的操作者必须是用户本人——界面必须提供可供真人点击/输入/拖拽"
            "的交互控件（按钮/表单/画布等），状态变化由用户操作驱动；③严禁 AI 自我演示、自问自答或与脚本/"
            "自动化程序互演，AI 只负责通过 MCP 工具读写共享状态、辅助用户（如校验输入、"
            "展示状态、提示规则），状态推进必须由用户亲手在浏览器里完成。**\n"
            "**工程质量硬性要求（违反会被自动自检拦下并打回重做）**：\n"
            "  · web_page 必须是一份**完整可独立运行**的单页 HTML（含 <html>/<head>/<body>），"
            "所有样式与脚本**全部内联**，严禁外网 CDN/字体/图片/接口依赖（离线必须可用）；\n"
            "  · 页面加载后立即 `fetch('/api/state')` 渲染当前状态；每次操作后刷新状态，"
            "并在页面上显示「操作结果/错误提示」区域（禁止静默失败）；\n"
            "  · **必须提供 `state` 端点（GET，无参数，返回当前状态的完整文本/JSON 摘要）**，"
            "供页面首屏与 LLM 读取；其余端点为动作/查询粒度（创建/推进/校验/回退/重置等）；\n"
            "  · 每个端点的 implementation：从 args dict 取参（数字/布尔要能容忍字符串），"
            "读写模块级共享状态 _STATE（dict），**返回可读文本**，"
            "所有异常自行 try/except 并返回错误说明文本（**绝不允许抛出**）；\n"
            "  · 端点数量 2-6 个，命名语义化（小写+下划线），读状态用 GET、写操作用 POST。\n"
            "输出严格 JSON（不要 markdown 代码块包裹），字段如下：\n"
            "{\n"
            '  "name": "插件名（英文，字母数字下划线连字符，≤50字符，小写）",\n'
            '  "summary": "一句话用途简介（中文）",\n'
            '  "skill_md": "（必填）该插件的 SKILL.md 正文（含 frontmatter 的 name/description，'
            '中文）：写清何时使用本插件、先 web_url+browser_open 打开界面给用户、'
            '严禁 AI 自我演示/与脚本互演、以及可用的 MCP 工具与浏览器操作如何配合",\n'
            '  "web_page": "（必填）完整单页 HTML 字符串（UTF-8，标题用插件名；操作界面直接用 '
            '<div>/<table>/canvas 绘制；页面内 JS 用 fetch 调用同源 /api/<端点名> 完成状态读写与操作；'
            '禁止引用外部 CDN/外网资源，全部内联；中文界面）",\n'
            '  "api": [（必填，2-6 个真实可用的 API 端点，每个自动映射为同名 MCP 工具 + HTTP /api/<name>）{\n'
            '    "name": "端点名（英文小写下划线，创建、推进、校验、回退、重置等动作/查询粒度）",\n'
            '    "description": "端点用途一句话（中文，供 LLM 与前端选择调用）",\n'
            '    "method": "GET|POST（读状态用 GET，操作写用 POST）",\n'
            '    "args": [{"name":"参数名","type":"integer|string|number|boolean","description":"参数说明"}...]（无参则 []）,\n'
            '    "implementation": "函数体 Python 代码字符串（用标准库实现真实操作：参数从 args dict 取，'
            '可读写模块级共享状态 _STATE（dict），如 _STATE 保存状态数组；返回 str 文本结果，'
            '如操作结果或当前状态文本；异常自行捕获并返回错误说明文本；'
            '不要包含 import 之外的框架代码）"\n'
            "  }],\n"
            '  "examples": {"示例文件名": "示例内容"}（可选）\n'
            "}\n"
        )
    else:
        sys_p = (
            common_rule +
            "例如流程编排类能力若不需要图形界面，应设计成细粒度工具集（创建任务/推进并校验前置/"
            "读取当前状态/回退）；若需要图形化界面，应改用 web 型插件（本地 Server + 浏览器界面）。\n"
            "输出严格 JSON（不要 markdown 代码块包裹），字段如下：\n"
            "{\n"
            '  "name": "插件名（英文，字母数字下划线连字符，≤50字符，小写）",\n'
            '  "summary": "一句话用途简介（中文）",\n'
            '  "skill_md": "（skill/combined 型必填）标准 SKILL.md 正文（含 frontmatter 的完整 markdown，'
            'name/description 字段），描述该插件技能触发条件与执行流程（中文）",\n'
            '  "tools": [（mcp/combined 型必填，1-5 个真实可用工具）{\n'
            '    "name": "工具名（英文小写下划线）",\n'
            '    "description": "工具用途一句话（中文，供 LLM 选择调用）",\n'
            '    "input_schema": {"type":"object","properties":{参数名:{"type":"string|integer|number|boolean",'
            '"description":"参数说明"}},"required":[必填参数名]}（参数为空则 properties:{} 无 required）,\n'
            '    "implementation": "该工具函数体的 Python 代码字符串（用标准库实现真实操作，'
            '函数名 def tool_xxx(args: dict)，从 args 取参，返回 str 文本结果；'
            '不要包含 import 之外的框架代码，异常自行捕获并返回错误说明文本"}\n'
            "  ],\n"
            '  "dependencies": ["（可选）工具实现所需第三方 pip 依赖，如 urllib 等标准库则省略"],\n'
            '  "examples": {"示例文件名": "示例内容"}（可选）\n'
            "}\n"
        )
    sys_p += f"用户描述: {desc}\n插件类型: {kind}"
    if kind == "web":
        sys_p += "（web 型需要 name/summary/skill_md/web_page/api 五项，其余可省）"
    elif kind == "skill":
        sys_p += "（skill 只需 name/summary/skill_md）"
    elif kind == "mcp":
        sys_p += "（mcp 只需 name/summary/tools）"
    else:
        sys_p += "（combined 需要 name/summary/skill_md/tools）"
    try:
        res = client.chat(
            [{"role": "system", "content": sys_p},
             {"role": "user", "content": desc}],
            max_tokens=8192, timeout=agent_llm.GEN_TIMEOUT_S)
        text = (res.get("text") or "").strip()
    except Exception:
        raise
    if not text:
        return {}
    # 提取 JSON（容忍 AI 包裹 ```json ... ```）
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}


def _split_impl(name: str, impl: str) -> tuple:
    """把 AI 返回的工具实现拆成 (嵌入代码, 调用函数名)。

    AI 可能返回两种形式：
      1. 完整函数定义：`def tool_xxx(args): ...` → 原样嵌入，函数名取 def 名
      2. 仅函数体：`text = args.get(...); return ...` → 包装为 def tool_{name}(args): ...
    保证生成代码语法正确（函数体相对 def 缩进 4 空格）。
    """
    impl = (impl or "").strip()
    if not impl:
        return "", ""
    lines = impl.splitlines()
    # 仅匹配首行 def 签名；body 从后续行完整保留（避免 \s* 吞掉换行导致缩进丢失）
    m = re.match(r"^\s*def\s+([A-Za-z_]\w*)\s*\([^)]*\)\s*(->[^:]*)?:\s*(.*)$", lines[0])
    if m:
        fname = m.group(1)
        inline = m.group(3).strip()
        body_lines = ([inline] if inline else []) + lines[1:]
        if not body_lines:
            return f"def {fname}(args: dict):\n    pass", fname
        nonempty = [ln for ln in body_lines if ln.strip()]
        min_ind = min(len(ln) - len(ln.lstrip()) for ln in nonempty)
        body = "\n".join((ln[min_ind:] if ln.strip() else ln) for ln in body_lines)
        body = "\n".join(("    " + ln) if ln.strip() else ln for ln in body.splitlines())
        return f"def {fname}(args: dict):\n{body}", fname
    # 仅函数体：包装为 def，统一缩进 4 空格
    lines = [ln for ln in impl.splitlines() if ln.strip()]
    if not lines:
        return f"def tool_{name}(args: dict):\n    pass", f"tool_{name}"
    indents = [len(ln) - len(ln.lstrip()) for ln in lines]
    min_ind = min(indents) if indents else 0
    body = "\n".join(("    " + ln[min_ind:]) for ln in lines)
    return f"def tool_{name}(args: dict):\n{body}", f"tool_{name}"


def _assemble_server_py(name: str, tools: list) -> str:
    """把 AI 设计的工具定义与实现，组装成符合 agent_mcp 客户端约定的 MCP stdio server。

    协议：newline-delimited JSON-RPC 2.0（stdin 读请求 / stdout 写响应），
    实现 initialize / notifications/initialized / ping / tools/list / tools/call。
    工具由 AI 提供 implementation（完整 def 或函数体，标准库实现真实操作），
    其余为固定骨架，保证与本地 McpClient 完全兼容。
    """
    tool_entries = []
    impls = []
    handlers = []
    for i, t in enumerate(tools or []):
        if not isinstance(t, dict):
            continue
        tname = str(t.get("name") or f"tool_{i}").strip()
        tdesc = str(t.get("description") or "").strip()
        schema = t.get("input_schema") or {"type": "object", "properties": {}}
        impl = str(t.get("implementation") or "").strip()
        if not tname or not impl:
            continue
        code, fn = _split_impl(tname, impl)
        if not code:
            continue
        tool_entries.append(
            f'    {{"name": {json.dumps(tname, ensure_ascii=False)}, '
            f'"description": {json.dumps(tdesc, ensure_ascii=False)}, '
            f'"inputSchema": {json.dumps(schema, ensure_ascii=False)}}},')
        impls.append(code)
        handlers.append(f'    "{tname}": {fn},')
    if not tool_entries:
        return ""
    tools_txt = "\n".join(tool_entries).rstrip(",")
    impls_txt = "\n\n".join(impls)
    handlers_txt = "\n".join(handlers)
    return f'''"""MCP server（stdio）: {name}

由 AI 设计工具实现 + 固定协议骨架组装，供 zhuzhu_Copilot 的 agent_mcp 客户端连接调用。
协议：newline-delimited JSON-RPC 2.0，stdin 读请求 / stdout 写响应（UTF-8）。
"""

import json
import sys


# ---------- 工具实现（真实 API/系统操作，AI 生成） ----------

{impls_txt}

# ---------- 工具注册表 ----------

TOOLS = [
{tools_txt}
]

_HANDLERS = {{
 {handlers_txt}
 }}
''' + _mcp_skeleton(name)


# 生成物统一的入口守卫（web 型需要在它**之前**插入自检函数：Python 按执行顺序解析，
# 守卫里调用的名字必须在守卫执行时已定义）
_MCP_ENTRY_GUARD = 'if __name__ == "__main__":\n    main()'


def _mcp_skeleton(name: str) -> str:
    """MCP JSON-RPC 分发骨架（_handle/main）：mcp 与 web 型 server.py 共用。
    TOOLS/_HANDLERS 由上层组装函数注入，此处只负责协议分发与启动主循环。"""
    return f'''
# ---------- JSON-RPC 分发 ----------

def _handle(msg: dict):
    """处理单个请求/通知；通知（无 id）返回 None 不回复"""
    method = msg.get("method", "")
    params = msg.get("params", {{}}) or {{}}
    rid = msg.get("id")

    if method == "initialize":
        return {{"jsonrpc": "2.0", "id": rid, "result": {{
            "protocolVersion": "2024-11-05",
            "capabilities": {{"tools": {{}}}},
            "serverInfo": {{"name": {json.dumps(name)}, "version": "1.0"}},
        }}}}
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {{"jsonrpc": "2.0", "id": rid, "result": {{}}}}
    if method == "tools/list":
        return {{"jsonrpc": "2.0", "id": rid, "result": {{"tools": TOOLS}}}}
    if method == "tools/call":
        tname = params.get("name", "")
        fn = _HANDLERS.get(tname)
        if fn is None:
            return {{"jsonrpc": "2.0", "id": rid,
                    "error": {{"code": -32601, "message": f"未知工具: {{tname}}"}}}}
        try:
            text = fn(params.get("arguments", {{}}) or {{}})
            result = {{"content": [{{"type": "text", "text": text}}], "isError": False}}
        except Exception as e:
            result = {{"content": [{{"type": "text", "text": str(e)}}], "isError": True}}
        return {{"jsonrpc": "2.0", "id": rid, "result": result}}
    return {{"jsonrpc": "2.0", "id": rid,
            "error": {{"code": -32601, "message": f"未知方法: {{method}}"}}}}


def main():
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    for line in stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            continue
        resp = _handle(msg)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False).encode("utf-8") + b"\\n")
            stdout.flush()


{_MCP_ENTRY_GUARD}
'''


# ── 生成后自检（web 型 server.py 内置的离线自检入口） ────────────────────────
# 为什么放进生成物本身：自检要起 HTTP、逐个打端点，还得在**目标环境**里跑（解释器/依赖
# 与运行时一致）。把入口写进 server.py 后，主程序只需 `python server.py --selftest` 并解析
# 最后一行 JSON，不必在外部重复一份「怎么调这个 server」的知识（新增端点自动被覆盖）。
# 输出走 stdout.buffer（utf-8 固定），避免 Windows 控制台编码把中文错误信息写坏。
_WEB_SELFTEST_SRC = '''

# ---------- 离线自检（生成后由主程序调用：python server.py --selftest） ----------

def _selftest():
    """起本地 HTTP，拉取 / 与每个 /api 端点，打印一行 JSON 结论后返回退出码。

    判据（任一不满足即视为不合格，错误信息会回灌给模型修正）：
      · 页面字节数 >= 200（挡住空壳/未生成 HTML）
      · 每个 /api 端点都能按声明的 GET/POST 调通，且返回 {"ok": true}
    """
    import urllib.request

    out = {"ok": True, "page_bytes": 0, "endpoints": {},
           "tools": [t.get("name") for t in TOOLS], "error": ""}
    try:
        _start_http()
        base = f"http://127.0.0.1:{_WEB_PORT}"
        with urllib.request.urlopen(base + "/", timeout=5) as r:
            out["page_bytes"] = len(r.read())
    except Exception as e:
        out["ok"], out["error"] = False, f"HTTP 服务或页面加载失败: {e}"

    if out["ok"]:
        for ep in _HANDLERS:
            if ep == "web_url":
                continue
            try:
                method = _API_METHODS.get(f"/api/{ep}", "POST")
                data = None if method == "GET" else b"{}"
                req = urllib.request.Request(base + f"/api/{ep}", data=data,
                                             method=method,
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    body = json.loads(r.read().decode("utf-8", "replace"))
                if body.get("ok"):
                    out["endpoints"][ep] = "ok"
                else:
                    out["ok"] = False
                    out["endpoints"][ep] = f"端点返回失败: {body.get('error')}"
            except Exception as e:
                out["ok"] = False
                out["endpoints"][ep] = f"端点调用异常: {e}"
    if out["ok"] and out["page_bytes"] < 200:
        out["ok"] = False
        out["error"] = f"页面仅 {out['page_bytes']} 字节，疑似未生成有效 HTML"

    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8") + b"\\n")
    sys.stdout.buffer.flush()
    return 0 if out["ok"] else 1
'''


def _assemble_web_server_py(name: str, web_page: str, api: list) -> str:
    """组装 web 型插件 server.py：本地 HTTP Server（浏览器 UI + /api/*）+ MCP stdio 工具。

    架构：AI 生成的单页 HTML（_PAGE）由 HTTP / 提供；API 端点与 MCP 工具共用同一批
    _HANDLERS（操作模块级 _STATE 共享状态）——浏览器（/api/<端点>）与 LLM
    （MCP tools/call）操控同一份状态。端口启动时自动分配（bind 0），
    自动注入 web_url 工具返回浏览器访问地址（AI 用 browser_open 打开给用户）。
    """
    impls, handlers, tools_entries, api_methods = [], [], [], []
    for i, e in enumerate(api or []):
        if not isinstance(e, dict):
            continue
        ename = str(e.get("name") or f"api_{i}").strip()
        edesc = str(e.get("description") or "").strip()
        emethod = "POST" if str(e.get("method") or "POST").upper() == "POST" else "GET"
        eimpl = str(e.get("implementation") or "").strip()
        eargs = [a for a in (e.get("args") or []) if isinstance(a, dict) and a.get("name")]
        if not ename or not eimpl:
            continue
        code, fn = _split_impl(ename, eimpl)
        if not code:
            continue
        props = {a["name"]: {"type": str(a.get("type") or "string"),
                             "description": str(a.get("description") or "")}
                 for a in eargs}
        schema = {"type": "object", "properties": props,
                  "required": [a["name"] for a in eargs]}
        tools_entries.append(
            f'    {{"name": {json.dumps(ename, ensure_ascii=False)}, '
            f'"description": {json.dumps(edesc, ensure_ascii=False)}, '
            f'"inputSchema": {json.dumps(schema, ensure_ascii=False)}}},')
        impls.append(code)
        handlers.append(f'    "{ename}": {fn},')
        api_methods.append(f'    "/api/{ename}": "{emethod}",')
    if not tools_entries:
        return ""
    # 注意：tools_txt 保留每项尾逗号——TOOLS 列表末尾紧接自动注入的 web_url 条目
    tools_txt = "\n".join(tools_entries)
    impls_txt = "\n\n".join(impls)
    handlers_txt = "\n".join(handlers)
    api_methods_txt = "\n".join(api_methods)
    page_literal = json.dumps(web_page or "", ensure_ascii=False)
    return f'''"""Web 插件（本地 Server + 浏览器 UI + MCP 工具）: {name}

- HTTP: ThreadingHTTPServer 提供 / （AI 生成的 HTML 页面）与 /api/<端点> （前端 AJAX）
- MCP:  stdio JSON-RPC，工具与 HTTP API 共用同一批 _HANDLERS（模块级 _STATE 共享状态）
浏览器与 LLM 操作同一份状态；web_url 工具返回浏览器访问地址（端口自动分配）。
"""

import json
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# ---------- 浏览器页面（AI 生成，全部内联，无外网依赖） ----------

_PAGE = {page_literal}


# ---------- 共享状态与端点/工具实现（AI 生成） ----------

_STATE = {{}}

{impls_txt}


_WEB_PORT = 0


def _web_url(args):
    return f"http://127.0.0.1:{{_WEB_PORT}}/"


# ---------- 端点/工具注册（浏览器 /api 与 LLM MCP 工具共用，含内置 web_url） ----------

_HANDLERS = {{
{handlers_txt}
    "web_url": _web_url,
 }}

_API_METHODS = {{
{api_methods_txt}
 }}


TOOLS = [
{tools_txt}
    {{"name": "web_url", "description": "返回该插件的浏览器访问地址，用 browser_open 打开给用户",
      "inputSchema": {{"type": "object", "properties": {{}}}}}},
]


# ---------- HTTP 服务（浏览器界面 + /api/*） ----------

def _conv(v):
    """GET 查询参数从字符串尽量还原为数字/布尔，失败保持原串"""
    try:
        return json.loads(v)
    except Exception:
        return v


class _HttpHandler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", ""):
            self._send(200, _PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if not parsed.path.startswith("/api/"):
            self._send(404, b'{{"ok": false, "error": "not found"}}')
            return
        args = {{k: _conv(v[0] if len(v) == 1 else v)
                for k, v in urllib.parse.parse_qs(parsed.query).items()}}
        self._call_api(parsed.path, args)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self._send(404, b'{{"ok": false, "error": "not found"}}')
            return
        try:
            ln = int(self.headers.get("Content-Length") or 0)
            args = json.loads(self.rfile.read(ln) or b"{{}}")
        except Exception:
            args = {{}}
        self._call_api(parsed.path, args if isinstance(args, dict) else {{}})

    def _call_api(self, path, args):
        ep = path[len("/api/"):].rstrip("/")
        fn = _HANDLERS.get(ep)
        if fn is None:
            self._send(404, b'{{"ok": false, "error": "unknown api"}}')
            return
        try:
            text = fn(args)
            body = json.dumps({{"ok": True, "result": str(text)}},
                              ensure_ascii=False).encode("utf-8")
            self._send(200, body)
        except Exception as e:
            body = json.dumps({{"ok": False, "error": str(e)}},
                              ensure_ascii=False).encode("utf-8")
            self._send(500, body)

    def log_message(self, *a):
        pass


def _start_http():
    global _WEB_PORT
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _HttpHandler)
    _WEB_PORT = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return _WEB_PORT
''' + _mcp_skeleton(name).replace(
        "def main():\n    stdin = sys.stdin.buffer\n    stdout = sys.stdout.buffer\n"
        "    for line in stdin:\n"
        "        if not line.strip():\n            continue\n"
        "        try:\n"
        "            msg = json.loads(line.decode(\"utf-8\", \"replace\"))\n"
        "        except json.JSONDecodeError:\n            continue\n"
        "        resp = _handle(msg)\n"
        "        if resp is not None:\n"
        "            stdout.write(json.dumps(resp, ensure_ascii=False).encode(\"utf-8\") + b\"\\n\")\n"
        "            stdout.flush()\n\n\n",
        "def main():\n"
        "    if \"--selftest\" in sys.argv:\n        sys.exit(_selftest())\n"
        "    _start_http()   # 先起本地 Web 服务，浏览器随时可访问\n"
        "    stdin = sys.stdin.buffer\n    stdout = sys.stdout.buffer\n"
        "    for line in stdin:\n"
        "        if not line.strip():\n            continue\n"
        "        try:\n"
        "            msg = json.loads(line.decode(\"utf-8\", \"replace\"))\n"
        "        except json.JSONDecodeError:\n            continue\n"
        "        resp = _handle(msg)\n"
        "        if resp is not None:\n"
        "            stdout.write(json.dumps(resp, ensure_ascii=False).encode(\"utf-8\") + b\"\\n\")\n"
        "            stdout.flush()\n\n\n").replace(
        _MCP_ENTRY_GUARD, _WEB_SELFTEST_SRC + "\n\n" + _MCP_ENTRY_GUARD, 1)


def skill_md_path(name: str) -> str:
    """插件内置 SKILL.md 路径（skill 型插件）"""
    return str(plugin_dir(name) / "SKILL.md")
