"""Copilot 插件系统：应用级独立插件体系（与 Agent 插件分开，应用范围更广）。

两套体系的分界：
- **Agent 插件**（`agent_plugins.py`，目录 `~/.zhuzhu_Copilot/plugins/`）：
  只扩展 AI Agent 的能力——MCP 工具、SKILL.md 技能、Agent 面板用的本地网页界面。
- **Copilot 插件**（本模块，目录 `~/.zhuzhu_Copilot/copilot_plugins/`）：
  扩展**整个 Copilot 应用本身**，覆盖所有功能模块：
    1. 源码级集成：`source_ops` 变更清单可把功能代码**接入任何现有模块**
       （按行号或锚点追加/插入 / 按行号区间替换 / 替换模块 / 整体替换），也可
       **新建功能模块**，支持一次多条、跨多个模块综合修改并集成功能；
    2. 声明式应用扩展：`copilot.json` 注册应用级能力——主窗口页面 / 后台服务 /
       托盘菜单动作 / 全局快捷键；
    3. 可选 `agent/` 子包：同时携带 MCP 工具与技能，成为 Agent 插件的超集。

插件包结构 `~/.zhuzhu_Copilot/copilot_plugins/<name>/`：
  plugin.json     元数据：kind=copilot / name / author 开发者或公司 /
                  updated_at 更新时间 / icon 插件图标 / description / enabled；
                  也可内嵌 source_ops
  source_ops.json 源码变更清单（与 plugin.json 内嵌 source_ops 二选一，内嵌优先）
  copilot.json    声明式应用扩展清单（pages / services / tray / shortcuts）
  agent/          可选 Agent 能力子包（SKILL.md / server.py，导入时登记）
  icon.*          插件图标（png/jpg/svg/ico…）
  config.json     用户配置（键值对标量；MCP 型子包注入环境变量）
  backups/        应用源码变更前的逐文件备份（apply 时生成）
  applied.json    已应用变更清单（revert 卸载回滚用）

安全（插件校验系统，内置唯一、不可替换）：所有 Copilot 插件在导入 / AI 生成 /
源码应用之前都必须通过 `plugin_guard` 校验（静态危险规则 + LLM 源码评审）；
判定危险的代码**强制删除**并明确提示用户，源码级插件危险则整体移除插件包。
校验系统自身、源码引擎、覆盖层导入钩子（_copilot_overlay_hook.py）与应用入口
（main.py）都在受保护清单，任何源码插件不得改动。

源码落点（源码运行 vs 打包运行自动切换，对插件透明）：
  · 源码运行（src/）：变更直接写入 src/ 下对应文件；
  · 打包运行（安装版）：变更写入用户数据目录的源码覆盖层
    ~/.zhuzhu_Copilot/source_overlay/（镜像源码相对路径），由导入钩子让
    zhuzhu_Copilot.* 的导入优先命中覆盖层——安装版同样支持改主题、自定义
    Agent、自定义工具、自定义语言、增删替换任何模块。

热插拔（硬承诺，不允许退化为"重启生效"）：本模块的启用/停用/删除/导入/AI 生成
全部经由 `core/hot_reload.py` 在运行进程内即时装载/卸载；热加载失败时**自动回滚**
该插件（revert 磁盘变更 + 强制热加载回滚结果），绝不给用户留半套生效变更。
声明式扩展（页面/服务/托盘/快捷键）通过 hot_reload 的 UI 事件桥由 main_window
即时挂摘；AI 工具链路（工作线程）触发时事件自动 marshal 到 GUI 线程执行。
"""

import json
import os
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path

from zhuzhu_Copilot import app_identity
from zhuzhu_Copilot.core import agent_runtime, agent_skills

COPILOT_PLUGINS_DIR = app_identity.data_root() / "copilot_plugins"

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")
_CONFIG_KEY_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_MAX_CONFIG_KEYS = 64
_MAX_CONFIG_VALUE = 4096
_ICON_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".ico", ".gif", ".webp"}
# 导入 zip 的大小/条目上限（防恶意包）
_MAX_ZIP_BYTES = 64 * 1024 * 1024
_MAX_ZIP_ENTRIES = 5000
# 导出时排除的本机运行时数据（备份/应用清单/缓存）
_EXPORT_EXCLUDE_DIRS = {"__pycache__", "backups", ".guard_tmp"}
_EXPORT_EXCLUDE_FILES = {"applied.json"}

# 与系统环境变量冲突的保留名（配置键不得覆盖，第二道防线）
_ENV_RESERVED = {
    "PATH", "HOME", "USER", "USERPROFILE", "SHELL", "TEMP", "TMP",
    "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMDATA",
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PYTHONPATH",
    "PYTHONHOME", "LANG", "LC_ALL", "TERM", "DISPLAY",
}


# ---------------------------------------------------------------------------
# 目录与元数据
# ---------------------------------------------------------------------------

def plugins_dir() -> Path:
    """Copilot 插件根目录（不存在则创建）"""
    try:
        COPILOT_PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return COPILOT_PLUGINS_DIR


def plugin_dir(name: str) -> Path:
    return plugins_dir() / name


def _read_meta(name: str) -> dict:
    d = plugin_dir(name)
    f = d / "plugin.json"
    if not f.is_file():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8-sig"))   # 兼容带 BOM 的元数据
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_meta(name: str, meta: dict) -> bool:
    d = plugin_dir(name)
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / "plugin.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def plugin_icon_path(name: str):
    """插件图标文件路径（plugin.json 的 icon 字段指向包内图标；无/非法返回 None）。"""
    icon = str((_read_meta(name) or {}).get("icon") or "").strip()
    if not icon or "/" in icon or "\\" in icon or icon.startswith("."):
        return None
    p = plugin_dir(name) / icon
    if p.is_file() and p.suffix.lower() in _ICON_EXTS:
        return p
    return None


def _normalize_pkg_meta(name: str) -> None:
    """导入/创建后补齐插件包规范字段：author / updated_at / icon / kind。"""
    meta = _read_meta(name)
    if not meta:
        return
    changed = False
    meta["kind"] = "copilot"
    changed = True
    if not str(meta.get("author") or "").strip():
        meta["author"] = ""
        changed = True
    if not str(meta.get("updated_at") or "").strip():
        meta["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        changed = True
    icon = str(meta.get("icon") or "").strip()
    if icon and plugin_icon_path(name) is None:
        meta["icon"] = ""
        changed = True
    if changed:
        _write_meta(name, meta)


def list_plugins() -> list:
    """扫描 Copilot 插件目录，返回元数据列表（含构成/应用状态/扩展摘要）"""
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
        meta.setdefault("kind", "copilot")
        meta.setdefault("enabled", True)
        meta.setdefault("created_at", "")
        meta.setdefault("author", "")
        meta.setdefault("updated_at", meta.get("created_at") or "")
        meta.setdefault("icon", "")
        d = plugin_dir(n)
        from zhuzhu_Copilot.core import plugin_source
        st = plugin_source.source_ops_status(d)
        meta["source_applied"] = bool(st.get("applied"))
        meta["source_ops"] = st.get("ops") or 0
        man = load_copilot_manifest(d)
        meta["has_manifest"] = bool(man)
        meta["manifest_ext"] = _manifest_summary(man)
        meta["has_agent_pkg"] = (d / "agent").is_dir()
        out.append(meta)
    return out


def get_plugin(name: str) -> dict:
    for p in list_plugins():
        if p.get("name") == name:
            return p
    return {}


def _manifest_summary(man: dict) -> dict:
    """扩展清单的能力摘要（UI 展示用）：{pages, services, tray, shortcuts} 计数。"""
    if not man:
        return {"pages": 0, "services": 0, "tray": 0, "shortcuts": 0}
    return {
        "pages": len(man.get("pages") or []),
        "services": len(man.get("services") or []),
        "tray": len(man.get("tray") or []),
        "shortcuts": len(man.get("shortcuts") or []),
    }


# ---------------------------------------------------------------------------
# 声明式应用扩展清单（copilot.json）
#
# {
#   "pages":      [{"id": "my-page", "title": "我的页面", "icon": "icon.png",
#                   "module": "pages/my_page.py", "widget": "MyPage"}],
#   "services":    [{"id": "bg-job", "title": "后台任务",
#                   "module": "services/bg_job.py", "entry": "start",
#                   "stop": "stop"}],
#   "tray":        [{"id": "quick-do", "title": "快速操作",
#                   "module": "pages/my_page.py", "action": "quick_do"}],
#   "shortcuts":   [{"id": "open-page", "title": "打开我的页面",
#                   "hotkey": "ctrl+alt+m",
#                   "module": "pages/my_page.py", "action": "on_hotkey"}]
# }
#
# module 指向插件包内模块文件，由主窗口启动时动态导入并调用其 entry/action；
# id 全局唯一（同 id 后注册者被拒绝，防插件互相顶掉）。
# ---------------------------------------------------------------------------

def _valid_module_rel(rel) -> bool:
    s = str(rel or "").strip()
    if not s or len(s) > 200:
        return False
    if s.startswith(("/", "\\", ".")) or ":" in s:
        return False
    return ".." not in s.replace("\\", "/").split("/")


def validate_copilot_manifest(man) -> tuple:
    """校验扩展清单结构。返回 (ok, msg)。"""
    if man is None:
        return True, ""
    if not isinstance(man, dict):
        return False, "copilot.json 必须是 JSON 对象"
    seen = set()
    for section in ("pages", "services", "tray", "shortcuts"):
        items = man.get(section)
        if items is None:
            continue
        if not isinstance(items, list):
            return False, f"copilot.json 的 {section} 必须是数组"
        for it in items:
            if not isinstance(it, dict):
                return False, f"{section} 的条目必须是对象"
            eid = str(it.get("id") or "").strip()
            if not _NAME_RE.match(eid or ""):
                return False, f"{section} 条目缺少合法 id（字母/数字/_-，≤50）"
            if eid in seen:
                return False, f"扩展 id「{eid}」重复"
            seen.add(eid)
            mod = it.get("module")
            if section != "tray" and not _valid_module_rel(mod):
                return False, f"{section}.{eid} 的 module 非法（包内相对路径）"
            if section == "pages":
                if not str(it.get("title") or "").strip():
                    return False, f"pages.{eid} 缺少 title"
                if not str(it.get("widget") or "").strip():
                    return False, f"pages.{eid} 缺少 widget（页面类名）"
                # 呈现方式：tab（浮层页签，默认）/ glass（独立无框液体玻璃窗口）
                mode = str(it.get("window") or "").strip().lower()
                if mode and mode not in ("tab", "glass"):
                    return False, f"pages.{eid} 的 window 只支持 tab / glass"
                for key in ("width", "height"):
                    if it.get(key) is None:
                        continue
                    try:
                        v = int(it.get(key))
                    except (TypeError, ValueError):
                        return False, f"pages.{eid} 的 {key} 必须是整数"
                    if not 240 <= v <= 4000:
                        return False, f"pages.{eid} 的 {key} 超出 240~4000"
            if section == "shortcuts":
                hk = str(it.get("hotkey") or "").strip()
                if not hk or len(hk) > 40 or not re.match(r"^[A-Za-z0-9+]+$", hk):
                    return False, f"shortcuts.{eid} 的 hotkey 非法（如 ctrl+alt+m）"
    return True, ""


def load_copilot_manifest(pkg_dir) -> dict:
    """读取并校验插件的 copilot.json 扩展清单；缺失/损坏返回 {}。"""
    d = Path(pkg_dir)
    f = d / "copilot.json"
    if not f.is_file():
        return {}
    try:
        man = json.loads(f.read_text(encoding="utf-8-sig"))   # 兼容带 BOM 的清单
    except (OSError, ValueError):
        return {}
    ok, _why = validate_copilot_manifest(man)
    return man if ok and isinstance(man, dict) else {}


def _iter_manifest_entries(man: dict):
    """展开清单为 (section, entry, plugin_name) 三元组（主窗口加载用）。"""
    for section in ("pages", "services", "tray", "shortcuts"):
        for it in (man.get(section) or []):
            yield section, it


# ---------------------------------------------------------------------------
# 插件配置（config.json：键值标量；MCP 型子包注入环境变量）
# ---------------------------------------------------------------------------

def get_plugin_config(name: str) -> dict:
    """读取插件配置；文件缺失/损坏/结构非法时返回 {}。"""
    f = plugin_dir(name) / "config.json"
    if not f.is_file():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8-sig"))   # 兼容带 BOM 的配置
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items()
            if isinstance(v, (str, int, float, bool))}


def _validate_config(cfg) -> tuple:
    if not isinstance(cfg, dict):
        return False, "配置必须是 JSON 对象（键值对）"
    if len(cfg) > _MAX_CONFIG_KEYS:
        return False, f"配置项超过 {_MAX_CONFIG_KEYS} 个上限"
    for k, v in cfg.items():
        key = str(k)
        if not _CONFIG_KEY_RE.match(key):
            return False, f"配置键「{key}」不合法（仅字母/数字/下划线/连字符/点，≤64 字符）"
        if key.upper() in _ENV_RESERVED:
            return False, f"配置键「{key}」会覆盖系统关键环境变量（PATH/HOME 等），已禁止"
        if isinstance(v, bool) or isinstance(v, (str, int, float)):
            if isinstance(v, str) and len(v) > _MAX_CONFIG_VALUE:
                return False, f"配置项「{key}」取值过长（>{_MAX_CONFIG_VALUE} 字符）"
            continue
        return False, f"配置项「{key}」的取值只支持标量（文本/数字/布尔）"
    return True, ""


def set_plugin_config(name: str, cfg: dict) -> tuple:
    """保存插件配置到 config.json；若插件登记了 stdio MCP 子包，同步更新其 env。"""
    if not plugin_dir(name).is_dir():
        return False, f"插件「{name}」不存在"
    ok, why = _validate_config(cfg)
    if not ok:
        return False, why
    try:
        (plugin_dir(name) / "config.json").write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        return False, f"写入插件配置失败: {e}"
    # 联动 MCP 子包：更新已登记服务器的 env（重连由上层触发）
    mcp_updated = False
    try:
        meta = _read_meta(name) or {}
        bound = meta.get("mcp_name")
        if bound:
            servers = agent_skills.load_mcp_servers()
            env = plugin_config_env(name)
            for s in servers:
                if s.get("name") == bound and s.get("type") == "stdio":
                    if env:
                        s["env"] = env
                    else:
                        s.pop("env", None)
                    mcp_updated = True
            if mcp_updated:
                agent_skills.save_mcp_servers(servers)
    except Exception:
        pass
    msg = f"插件「{name}」配置已保存"
    if mcp_updated:
        msg += "；MCP 环境变量已更新，重连后生效"
    return True, msg


def plugin_config_env(name: str) -> dict:
    """配置 → MCP 环境变量（键名大写，bool→true/false；跳过保留环境变量名）。"""
    env = {}
    for k, v in get_plugin_config(name).items():
        key = str(k)
        if key.upper() in _ENV_RESERVED:
            continue
        if isinstance(v, bool):
            env[key.upper()] = "true" if v else "false"
        else:
            env[key.upper()] = str(v)
    return env


# ---------------------------------------------------------------------------
# 源码级变更：应用 / 回滚 / 状态（引擎在 plugin_source，包目录级通用）
# ---------------------------------------------------------------------------

def apply(name: str) -> tuple:
    """应用 Copilot 插件的源码变更（校验 → 备份 → 应用 → 编译检查）。"""
    from zhuzhu_Copilot.core import plugin_source
    return plugin_source.apply_source_ops(plugin_dir(name), name)


def revert(name: str) -> tuple:
    """卸载回滚已应用的源码变更（新建文件删除、修改文件还原备份）。"""
    from zhuzhu_Copilot.core import plugin_source
    return plugin_source.revert_source_ops(plugin_dir(name), name)


def status(name: str) -> dict:
    """源码变更应用状态：{applied, applied_at, ops, files}。"""
    from zhuzhu_Copilot.core import plugin_source
    return plugin_source.source_ops_status(plugin_dir(name))


def source_ops(name: str) -> list:
    """插件的源码变更清单（source_ops.json 或 plugin.json 内嵌）。"""
    from zhuzhu_Copilot.core import plugin_source
    return plugin_source.load_source_ops_from(plugin_dir(name))


def overlay_info() -> dict:
    """当前源码落点信息（UI/诊断用）：
    {mode: source|overlay, root: 写入根目录, modules: 覆盖层已生效的模块清单}。"""
    from zhuzhu_Copilot.core import plugin_source
    root, mode = plugin_source.apply_root()
    mods = []
    if mode == "overlay":
        try:
            import _copilot_overlay_hook
            mods = _copilot_overlay_hook.overlay_modules()
        except Exception:
            mods = []
    return {"mode": mode, "root": str(root), "modules": mods}


# ---------------------------------------------------------------------------
# 启用 / 停用 / 删除
# ---------------------------------------------------------------------------

def set_enabled(name: str, enabled: bool) -> tuple:
    """启用/停用插件并**即时热插拔**（绝对不需要重启应用）。

    启用流程：source_ops 写入转译层 → 热加载引擎在运行进程内装载 → 失败自动回滚
    （revert + 强制热加载回滚结果）→ 成功后 UI 事件桥挂页面/服务/托盘/快捷键；
    停用流程：先通过 UI 事件桥摘除页面/服务/托盘/快捷键 → revert 磁盘 →
    热加载引擎立即卸载（新建模块直接摘出 sys.modules，修改模块还原回原版）。

    保证两件事：① 转译层内容 == 启用中的插件；② 任何时候系统要么生效、要么
    回滚，不存在"重启一下才生效"的半套变更。
    """
    from zhuzhu_Copilot.core import hot_reload

    meta = _read_meta(name)
    if not meta:
        return False, f"插件「{name}」不存在"
    enabled = bool(enabled)

    if enabled:
        # 1) 磁盘事务：写入转译层（内部自带校验/备份/编译检查，失败一行未动）
        if source_ops(name) and not status(name).get("applied"):
            ok, msg = apply(name)
            if not ok:
                return False, f"插件「{name}」启用失败：{msg}"
        # 2) 热加载：运行进程内即时装载（预编译失败不动任何模块；exec 失败中止后续）
        res = hot_reload.reload_overlay()
        if res.get("failed"):
            # 3) 热加载失败 → 自动回滚（磁盘 + 运行态），绝不留半套变更
            try:
                revert(name)
            except Exception:
                pass
            hot_reload.reload_overlay(force=True)
            hot_reload.emit_ui("after_change", name)
            bad_meta = _read_meta(name) or meta
            bad_meta["enabled"] = False
            bad_meta["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _write_meta(name, bad_meta)
            detail = "；".join(res["failed"][:3])
            return False, (f"插件「{name}」热加载失败，已自动回滚（无需重启）：{detail}。"
                           "请检查插件代码后重新启用")
        # 4) 声明式扩展挂装（页面/服务/托盘/快捷键）；GUI 线程同步、工作线程自动 marshal
        hot_reload.emit_ui("after_enable", name)
        # 5) 写元数据
        meta = _read_meta(name) or meta
        meta["enabled"] = True
        meta["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        if not _write_meta(name, meta):
            return False, "写入插件元数据失败"
        hint = hot_reload.summary(res)
        return True, f"插件「{name}」已启用并即时生效（无需重启）：{hint}"

    # 停用：先摘 UI/服务，再回滚磁盘并热卸载
    hot_reload.emit_ui("before_disable", name)
    if status(name).get("applied"):
        ok, msg = revert(name)
        if not ok:
            return False, f"插件「{name}」停用失败：{msg}"
    res = hot_reload.reload_overlay()
    hot_reload.emit_ui("after_change", name)
    meta = _read_meta(name) or meta
    meta["enabled"] = False
    meta["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if not _write_meta(name, meta):
        return False, "写入插件元数据失败"
    hint = hot_reload.summary(res)
    return True, f"插件「{name}」已停用并即时卸载（无需重启）：{hint}"


def delete_plugin(name: str) -> tuple:
    """删除插件：先摘 UI/服务 → 已应用源码回滚 → 清理 agent 子包 → 删目录 →
    热加载即时卸载（死模块摘除、修改模块还原）。"""
    from zhuzhu_Copilot.core import hot_reload
    import shutil
    d = plugin_dir(name)
    if not d.is_dir():
        return False, f"插件「{name}」不存在"
    # 1) 先摘页面/服务/托盘/快捷键（避免服务还持着插件代码跑）
    hot_reload.emit_ui("before_disable", name)
    parts = []
    # 2) 磁盘回滚
    if (d / "applied.json").is_file():
        ok, msg = revert(name)
        parts.append(msg)
    # 3) agent 子包登记清理
    _unregister_agent_pkg(name)
    # 4) 删目录
    try:
        shutil.rmtree(d)
    except OSError as e:
        return False, f"删除失败: {e}"
    # 5) 热加载：死模块立即摘除、被改模块还原为原版
    res = hot_reload.reload_overlay()
    hot_reload.emit_ui("after_change", name)
    parts.append(f"已删除 Copilot 插件「{name}」并即时卸载（{hot_reload.summary(res)}）")
    return True, "；".join(parts)


# ---------------------------------------------------------------------------
# agent/ 子包：把 Agent 能力（MCP 工具 + SKILL.md 技能）登记进 Agent 体系
# ---------------------------------------------------------------------------

def _parse_skill_md(text: str) -> dict:
    """极简解析 SKILL.md frontmatter（name/description）+ 正文。"""
    s = (text or "").strip()
    out = {"name": "", "description": "", "instruction": s}
    if s.startswith("---"):
        parts = s.split("---", 2)
        if len(parts) >= 3:
            fm, body = parts[1], parts[2]
            for line in fm.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip().lower()
                    if k in ("name", "description"):
                        out[k] = v.strip().strip('"').strip("'")
            out["instruction"] = body.strip()
    if not out["name"]:
        out["name"] = "copilot-agent-skill"
    return out


def _mcp_config_for_agent_pkg(name: str, meta: dict) -> dict:
    """按 agent 子包生成 MCP 服务器配置（stdio 跑 server.py，可带 env）。"""
    d = plugin_dir(name) / "agent"
    command = str(meta.get("mcp_command") or "").strip()
    if command:
        args = [str(x) for x in (meta.get("mcp_args") or [])]
    else:
        command = agent_runtime.python_interpreter()
        args = [str(d / "server.py")]
    cfg = {"name": f"{name}-mcp", "type": "stdio",
           "command": command, "args": args}
    env = plugin_config_env(name)
    if env:
        cfg["env"] = env
    return cfg


def _register_agent_pkg(name: str) -> tuple:
    """登记 agent 子包：SKILL.md → 技能；server.py/deploy.py 或远程 SSE → MCP。"""
    d = plugin_dir(name) / "agent"
    if not d.is_dir():
        return True, ""
    meta = _read_meta(name) or {}
    parts = []
    skill_md = d / "SKILL.md"
    if skill_md.is_file():
        try:
            s = _parse_skill_md(skill_md.read_text(encoding="utf-8", errors="replace"))
            skill_name = str(meta.get("skill_name") or s.get("name") or name).strip()
            ok, msg = agent_skills.create_md_skill(
                skill_name, s.get("description") or meta.get("description") or name,
                s.get("instruction"))
            if ok:
                meta.setdefault("skill_name", skill_name)
                parts.append(f"技能 /{skill_name} 已登记")
            else:
                parts.append(f"技能登记失败: {msg}")
        except Exception:
            parts.append("技能登记异常")
    has_server = (d / "server.py").is_file() or (d / "deploy.py").is_file()
    has_sse = (str(meta.get("mcp_type") or "").lower() == "sse"
               and str(meta.get("mcp_url") or "").strip())
    if has_server or has_sse:
        if has_sse:
            cfg = {"name": f"{name}-mcp", "type": "sse",
                   "url": str(meta.get("mcp_url") or "").strip()}
        else:
            cfg = _mcp_config_for_agent_pkg(name, meta)
        meta.setdefault("mcp_name", cfg["name"])
        try:
            servers = agent_skills.load_mcp_servers()
            if not any(s.get("name") == cfg["name"] for s in servers):
                servers.append(cfg)
                agent_skills.save_mcp_servers(servers)
            parts.append(f"MCP {cfg['name']}（{cfg.get('type')}）已登记")
        except Exception:
            parts.append("MCP 登记异常")
    _write_meta(name, meta)
    return True, "；".join(parts)


def _unregister_agent_pkg(name: str) -> None:
    """删除插件时清理 agent 子包登记（技能 + MCP 服务器）。"""
    meta = _read_meta(name) or {}
    skill_name = meta.get("skill_name")
    if skill_name:
        try:
            agent_skills.delete_skill(str(skill_name))
        except Exception:
            pass
    mcp_name = meta.get("mcp_name")
    if mcp_name:
        try:
            servers = [s for s in agent_skills.load_mcp_servers()
                       if s.get("name") != mcp_name]
            agent_skills.save_mcp_servers(servers)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 导入 / 导出
# ---------------------------------------------------------------------------

def import_plugin_zip(zip_path: str) -> tuple:
    """导入 Copilot 插件 zip 包（含 plugin.json + source_ops/copilot.json）。

    落盘 → 规范字段补齐 → **插件校验系统**（危险即强制删除并提示）→
    登记 agent 子包（如有）→ 启用状态下自动写入转译层。返回 (ok, message)。
    """
    p = Path(zip_path or "")
    if not p.is_file():
        return False, "文件不存在"
    try:
        if p.stat().st_size > _MAX_ZIP_BYTES:
            return False, "插件包过大（>64MB），已拒绝"
    except OSError:
        return False, "无法读取插件包"
    root = plugins_dir()
    # 1. 试读 plugin.json 拿名字（zip 内可能多一层目录）
    try:
        with zipfile.ZipFile(p) as zf:
            names = zf.namelist()
            if len(names) > _MAX_ZIP_ENTRIES:
                return False, "插件包条目过多，已拒绝"
            top = None
            for n in names:
                if n.endswith("plugin.json"):
                    top = n[:-len("plugin.json")].rstrip("/")
                    break
            if top is None:
                return False, "插件包缺少 plugin.json"
            meta = json.loads(zf.read((top + "/plugin.json") if top
                                      else "plugin.json").decode("utf-8-sig"))
            if not isinstance(meta, dict):
                return False, "plugin.json 格式错误"
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        return False, f"插件包解析失败: {e}"
    name = str(meta.get("name") or "").strip().lower()
    if not _NAME_RE.match(name or ""):
        return False, "plugin.json 的 name 不合法（字母/数字/下划线/连字符，≤50）"
    if (root / name).exists():
        return False, f"Copilot 插件「{name}」已存在"
    target = root / name
    # 2. 解包（防路径穿越）
    try:
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(p) as zf:
            for n in zf.namelist():
                rel = n[len(top):].lstrip("/") if (top and n.startswith(top + "/")) else n
                if not rel or rel.endswith("/"):
                    continue
                dest = (target / rel).resolve()
                if not str(dest).startswith(str(target.resolve())):
                    continue          # 穿越条目直接跳过
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(n) as src, open(dest, "wb") as out:
                    out.write(src.read())
    except (OSError, zipfile.BadZipFile) as e:
        try:
            import shutil
            shutil.rmtree(target, ignore_errors=True)
        except Exception:
            pass
        return False, f"解包失败: {e}"
    # 3. 规范字段 + 扩展清单校验
    _normalize_pkg_meta(name)
    meta = _read_meta(name)
    meta["kind"] = "copilot"
    _write_meta(name, meta)
    man = load_copilot_manifest(target)
    if (target / "copilot.json").is_file() and not man:
        return False, "copilot.json 扩展清单不合法，已拒绝导入"
    # 4. 插件校验系统（导入前最后一道闸）
    from zhuzhu_Copilot.core import plugin_guard
    gok, gmsg = plugin_guard.guard_source_package(target, name)
    if not gok:
        try:
            import shutil
            shutil.rmtree(target, ignore_errors=True)
        except Exception:
            pass
        return False, gmsg
    # 5. 登记 agent 子包
    ok, amsg = _register_agent_pkg(name)
    n_ops = len(source_ops(name))
    n_files = len({str(op.get("path") or "") for op in source_ops(name)})
    ext = _manifest_summary(man)
    msg = f"已导入 Copilot 插件「{name}」"
    if n_ops:
        msg += f"（{n_files} 个源码文件、{n_ops} 条变更）"
    if any(ext.values()):
        msg += ("；应用扩展：" +
                "、".join(f"{k}×{v}" for k, v in ext.items() if v))
    if amsg:
        msg += f"；{amsg}"
    # 6. 导入即生效：启用状态下自动写入转译层（与「启用」按钮同一套逻辑）
    meta = _read_meta(name) or {}
    if meta.get("enabled", True):
        eok, emsg = set_enabled(name, True)
        if not eok:
            failed = _read_meta(name) or meta
            failed["enabled"] = False
            _write_meta(name, failed)
            return False, f"{msg}，但自动启用失败：{emsg}（插件已保持停用）"
        if n_ops:
            msg += "。变更已即时热加载生效（无需重启）"
        else:
            msg += "。该插件只含运行时扩展，已即时启用"
    elif n_ops:
        msg += "。当前为停用状态，启用时会即时热加载生效"
    return True, msg


def export_plugin_zip(name: str, dest_path: str) -> tuple:
    """导出 Copilot 插件为 zip 分享包（含配置/图标/清单，排除本机运行时数据）。"""
    import shutil
    d = plugin_dir(name)
    if not d.is_dir():
        return False, f"插件「{name}」不存在"
    dest = Path(dest_path or "")
    if not str(dest):
        return False, "缺少导出路径"
    if dest.suffix.lower() != ".zip":
        dest = dest.with_suffix(".zip")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(d.rglob("*")):
                if not f.is_file():
                    continue
                rel = f.relative_to(d)
                if any(part in _EXPORT_EXCLUDE_DIRS for part in rel.parts):
                    continue
                if rel.name in _EXPORT_EXCLUDE_FILES:
                    continue
                zf.write(f, str(Path(name) / rel))
    except OSError as e:
        return False, f"导出失败: {e}"
    return True, f"已导出 Copilot 插件「{name}」→ {dest}"


def guard_log(name: str, limit: int = 50) -> list:
    """该校验系统对该插件的校验记录（插件校验系统内置日志，只读）。"""
    from zhuzhu_Copilot.core import plugin_guard
    events = plugin_guard.recent_guard_events(max(limit, 200))
    return [e for e in events if str(e.get("plugin") or "") == name][:limit]


# ---------------------------------------------------------------------------
# AI 生成（自然语言 → 源码变更清单 + 应用扩展清单）
# ---------------------------------------------------------------------------

_COPILOT_OP_RULES = (
    "可用 op（path 均为相对应用源码根（src/）的路径，"
    "如 zhuzhu_Copilot/core/xxx.py、zhuzhu_Copilot/ui/xxx.py）：\n"
    '  create_file     新建功能模块：{"op":"create_file","path":"...","content":"完整文件内容"}\n'
    '  append          追加到现有代码模块之后：{"op":"append","path":"...","content":"...",'
    '"after":"锚点串(可选，缺省追加到文件末尾)"}\n'
    '  insert_after    锚点/行号后插入：{"op":"insert_after","path":"...",'
    '"anchor":"锚点串(唯一)","content":"..."} 或 {"op":"insert_after","path":"...","line":120,"content":"..."}\n'
    '  insert_at       在第 N 行之前插入（行号 1 起，N=行数+1 即追加到末尾）：'
    '{"op":"insert_at","path":"...","line":120,"content":"..."}\n'
    '  replace_lines   替换第 S..E 行（含边界，1 起）：{"op":"replace_lines","path":"...",'
    '"start_line":100,"end_line":120,"content":"..."}\n'
    '  replace_module  替换模块：{"op":"replace_module","path":"...",'
    '"start":"起始锚点","end":"结束锚点(可选，缺省到文件尾)","content":"..."} 或 '
    '{"op":"replace_module","path":"...","start_line":100,"end_line":120,"content":"..."}\n'
    '  replace_file    整体替换文件：{"op":"replace_file","path":"...","content":"..."}\n'
    "硬性要求：\n"
    "  · 优先新建模块（create_file）再在现有模块里做最小侵入接线"
    "（append/insert_after/insert_at），不得大段重写无关代码；\n"
    "  · 定位方式二选一：**行号**（line / start_line..end_line，1 起含边界——插件作者"
    "拿得到完整源码（如 GitHub 仓库）时最直接）或**文本锚点**（anchor / start，须真实"
    "存在且唯一，对版本差异更鲁棒）；设计前先 read_file 看清目标文件内容与行号；\n"
    "  · 严禁改动受保护核心程序：plugin_guard.py、security.py、security_engine/、"
    "plugin_source.py、copilot_plugins.py、hot_reload.py、_copilot_overlay_hook.py、main.py；\n"
    "  · 代码用 Python 3.13 标准库或已在依赖清单内的库，风格与目标文件一致；\n"
    "  · 每条 op 的 content 必须是完整、可独立编译的代码；\n"
    "  · 可以一次设计多条 op、跨多个功能模块综合修改并集成功能。\n"
)

_COPILOT_MANIFEST_RULES = (
    "可选 copilot.json 应用扩展清单（应用级能力，按需提供，没有就省略该字段）：\n"
    '  "copilot": {"pages": [{"id":"...","title":"页面标题","icon":"icon.png",'
    '"module":"pages/xxx.py","widget":"XxxPage"}],\n'
    '             "services": [{"id":"...","title":"后台服务","module":"services/xxx.py",'
    '"entry":"start","stop":"stop"}],\n'
    '             "tray": [{"id":"...","title":"托盘动作","module":"pages/xxx.py",'
    '"action":"quick_do"}],\n'
    '             "shortcuts": [{"id":"...","title":"快捷键说明","hotkey":"ctrl+alt+m",'
    '"module":"pages/xxx.py","action":"on_hotkey"}]}\n'
    "module 指向插件包内模块文件（需用 create_file 一并创建）；页面 widget 为"
    " QWidget 子类类名；服务 entry/stop 为模块级函数名。\n"
)

#: AI 生成图标（SVG 源码）的规则：插件包里的图标文件由应用直接落盘
_COPILOT_ICON_RULES = (
    "可选插件图标 icon_svg（强烈建议提供，插件页卡片会显示）：一段**独立完整**的 SVG 源码，"
    "根元素必须带 xmlns=\"http://www.w3.org/2000/svg\" 与 viewBox=\"0 0 128 128\"，"
    "尺寸写 width=\"128\" height=\"128\"；风格要求：圆角方形底色（深色，如 #1E3A8A 系）+ "
    "1~3 个简洁图形，纯色填充、不引用外部资源/字体/脚本/滤镜，≤4KB；"
    "图形要能在 44px 下辨认（别用细线和小字）。\n"
)


def _ai_generate_copilot_spec(desc: str) -> dict:
    """调用当前配置的 LLM 生成 Copilot 插件设计 JSON
    （name/summary/author/source_ops/copilot）。"""
    from zhuzhu_Copilot.core import agent_llm
    cfg = agent_llm.load_model_config()
    client = agent_llm.LLMClient(cfg.get("base_url"), cfg.get("api_key"),
                                 cfg.get("model") or agent_llm.DEFAULT_MODEL)
    sys_p = (
        "你是 Copilot 应用级插件设计专家。Copilot 插件与 Agent 插件分开，"
        "扩展范围覆盖整个应用：既可以把功能代码接入任何现有模块/新建功能模块文件"
        "（跨多文件综合修改），也可以注册应用级能力（主窗口页面/后台服务/托盘/快捷键）。\n"
        + _COPILOT_OP_RULES + _COPILOT_MANIFEST_RULES + _COPILOT_ICON_RULES +
        '输出严格 JSON（不要 markdown 代码块包裹）：\n'
        '{\n'
        '  "name": "插件名（英文，字母数字下划线连字符，≤50字符，小写）",\n'
        '  "summary": "一句话用途简介（中文）",\n'
        '  "author": "开发者/公司名（未知可留空）",\n'
        '  "icon_svg": "上述图标 SVG 源码（可选但建议提供）",\n'
        '  "source_ops": [上述 op 对象，1-N 条，可跨多个文件],\n'
        '  "copilot": {上述扩展清单，可选}\n'
        "}\n"
        f"用户描述: {desc}"
    )
    res = client.chat(
        [{"role": "system", "content": sys_p},
         {"role": "user", "content": desc}],
        max_tokens=8192, timeout=agent_llm.GEN_TIMEOUT_S)
    text = (res.get("text") or "").strip()
    if not text:
        return {}
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


def _write_icon_svg(pkg_dir, svg) -> str:
    """把 AI 生成的 SVG 图标写入插件包，返回文件名（不可用返回空串）。

    图标只是静态图形，因此**拒绝**脚本 / 事件属性 / 外部引用 / 位图嵌套：
    这类内容对图标毫无用处，却正是「插件夹带可执行内容」的常见入口。
    """
    s = str(svg or "").strip()
    if not s or len(s) > 20480:
        return ""
    low = s.lower()
    if not low.startswith("<svg") or "</svg>" not in low or "xmlns" not in low:
        return ""
    for bad in ("<script", "javascript:", "onload=", "onerror=", "onclick=",
                "<foreignobject", "<image", 'href="http', "href='http",
                "<iframe", "<use"):
        if bad in low:
            return ""
    try:
        (Path(pkg_dir) / "icon.svg").write_text(s, encoding="utf-8")
        return "icon.svg"
    except OSError:
        return ""


# ══════════════ 骨架：由主 Agent 自己写源码变更（工具内不调用任何大模型） ══════════════
#
# 用户要求：对话里生成 Copilot 插件不能再「AI 调 AI」。这里只落骨架与规范，
# 变更清单由主 Agent 用 read_file / write_file / edit_file 自己写，然后
# apply_copilot_plugin 落地（校验 → 转译层 → 热加载）。
# create_from_nl 仅供设置页「AI 创建」按钮使用。

_COPILOT_README = """# Copilot 插件 {name} 实现说明

> 本插件由**主 Agent 自己实现**：按本文件规范用 read_file / write_file / edit_file
> 写 `source_ops.json` 与包内模块，然后调用 `apply_copilot_plugin(name="{name}")`
> 落地（校验 → 转译层 → 热加载）。禁止再调用任何「AI 生成插件」类工具或外包给子 Agent。

## 目录
- `plugin.json`：元数据（name / kind=copilot / enabled / description）。
- `source_ops.json`：源码变更清单（必填，非空数组）。
- `copilot.example.json`：应用级扩展清单示例（页面/服务/托盘/快捷键），需要时复制为
  `copilot.json` 再改；`module` 必须指向**本插件包内由你自己创建**的模块文件
  （字段名固定为 `id` / `module` / `widget` / `entry` / `action` / `hotkey`）。
- `agent/`（可选）：`SKILL.md` + `server.py`，导入时登记为技能与 MCP。

## source_ops 七种写法（可混用，单插件 ≤50 条）
```json
[
  {"op": "create_file", "path": "zhuzhu_Copilot/ui/my_feature.py", "content": "..."},
  {"op": "insert_after", "path": "zhuzhu_Copilot/ui/agent_panel.py",
   "anchor": "def _build_ui(self):", "content": "        self._my_hook()\\n"},
  {"op": "insert_at", "path": "zhuzhu_Copilot/core/agent_tools.py",
   "line": 120, "content": "..."},
  {"op": "append", "path": "zhuzhu_Copilot/ui/agent_panel.py", "content": "\\n# ...\\n"},
  {"op": "replace_lines", "path": "zhuzhu_Copilot/core/agent_tools.py",
   "start_line": 120, "end_line": 126, "content": "..."},
  {"op": "replace_module", "path": "zhuzhu_Copilot/ui/styles.py",
   "start": "def apply_theme()", "end": "def apply_palette(", "content": "..."},
  {"op": "replace_file", "path": "zhuzhu_Copilot/ui/my_font.py", "content": "..."}
]
```
- 路径必须是**应用源码根内的相对路径**（禁绝对路径 / `..`）。
- 优先 `create_file` 新建模块，再在现有模块做最小接线；改已有代码时要保证可编译。
- `insert_after` 的 `anchor` 必须真实存在且唯一（或改用 `line`）。

## 落地与生效
`apply_copilot_plugin` 会：插件校验系统（静态规则 + 源码评审）→ 备份 → 写入转译层 →
逐文件编译检查（失败整体回滚）→ 热加载引擎在运行进程内即时装载（**无需重启应用**）。
改错了用 `revert_copilot_plugin` 即时回滚。
"""

_COPILOT_MANIFEST_EXAMPLE = {
    "pages": [{"id": "my-page", "title": "示例页面",
               "module": "pages/my_page.py", "widget": "MyPage"}],
    "services": [{"id": "my-service", "title": "示例服务",
                  "module": "service/my_service.py", "entry": "start", "stop": "stop"}],
    "tray": [{"id": "my-tray", "title": "示例动作",
              "module": "pages/my_page.py", "action": "run"}],
    "shortcuts": [{"id": "my-hotkey", "title": "示例快捷键", "hotkey": "ctrl+alt+m",
                   "module": "pages/my_page.py", "action": "run"}],
}


def scaffold(name: str, description: str = "") -> tuple:
    """创建 Copilot 插件骨架（不调用 AI）：元数据 + 空变更清单 + 规范 README + 扩展清单示例。"""
    name = str(name or "").strip().lower()
    if not _NAME_RE.match(name or ""):
        return False, "插件名仅支持小写字母 / 数字 / 下划线 / 连字符（≤50 字符）"
    if plugin_dir(name).exists():
        return False, f"Copilot 插件「{name}」已存在：直接改文件后调用 apply_copilot_plugin 即可"
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    meta = {"name": name, "kind": "copilot", "enabled": False,
            "description": str(description or "").strip() or f"{name}（待实现）",
            "author": "", "created_at": now, "updated_at": now, "icon": ""}
    d = plugin_dir(name)
    try:
        d.mkdir(parents=True, exist_ok=True)
        _write_meta(name, meta)
        (d / "source_ops.json").write_text("[]\n", encoding="utf-8")
        (d / "copilot.example.json").write_text(
            json.dumps(_COPILOT_MANIFEST_EXAMPLE, ensure_ascii=False, indent=2),
            encoding="utf-8")
        (d / "README.md").write_text(_COPILOT_README.replace("{name}", name),
                                     encoding="utf-8")
    except OSError as e:
        return False, f"创建 Copilot 插件骨架失败: {e}"
    msg = (f"已创建 Copilot 插件骨架「{name}」：\n"
           f"- {d / 'plugin.json'}\n- {d / 'source_ops.json'}\n"
           f"- {d / 'README.md'}\n- {d / 'copilot.example.json'}\n\n"
           "下一步（必须由你自己完成，不要再调用任何 AI 生成类工具/子 Agent）：\n"
           f"1) read_file(\"{d / 'README.md'}\") 读 op 写法与约束；\n"
           "2) 用 read_file 看要接入的源码，按需 create_file 新模块并用 insert_after / "
           "replace_lines 做最小接线；把清单写进 source_ops.json；\n"
           "3) 需要页面/服务/托盘/快捷键时，把 copilot.example.json 复制为 copilot.json 并改 "
           "module 指向包内文件；\n"
           f"4) 调 apply_copilot_plugin(name=\"{name}\") 落地 + 热加载（无需重启应用）。")
    return True, msg


def create_from_nl(description: str, on_status=None) -> tuple:
    """用自然语言描述创建 Copilot 插件：AI 设计 source_ops（+应用扩展清单）→
    落盘插件包 → **插件校验系统**（危险即强制删除并提示）→ 自动写入转译层。

    返回 (ok, message)。创建即生效，无需再手动应用。
    """
    def rep(pct, msg):
        if callable(on_status):
            try:
                on_status(int(pct), msg)
            except Exception:
                pass

    desc = (description or "").strip()
    if not desc:
        return False, "请用自然语言描述你想要的 Copilot 插件功能"
    rep(5, "正在分析需求并设计源码变更清单…")
    try:
        spec = _ai_generate_copilot_spec(desc)
    except Exception as e:
        return False, f"AI 生成失败: {e}"
    if not spec:
        return False, "AI 未返回有效的 Copilot 插件设计"
    name = str(spec.get("name") or "").strip().lower()
    if not _NAME_RE.match(name or ""):
        return False, "AI 返回的插件名不合法，请换一种描述重试"
    if plugin_dir(name).exists():
        return False, f"Copilot 插件「{name}」已存在，请换名或删除后重试"
    ops = spec.get("source_ops")
    if not isinstance(ops, list) or not ops:
        return False, "AI 未返回 source_ops 变更清单"
    from zhuzhu_Copilot.core import plugin_source
    ok, why = plugin_source.validate_source_ops(ops)
    if not ok:
        return False, f"AI 返回的变更清单不合法：{why}"
    copilot = spec.get("copilot")
    if copilot is not None:
        mok, mwhy = validate_copilot_manifest(copilot)
        if not mok:
            return False, f"AI 返回的应用扩展清单不合法：{mwhy}"

    rep(60, "正在写入 Copilot 插件包…")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    meta = {"name": name, "kind": "copilot", "enabled": True,
            "description": str(spec.get("summary") or desc),
            "author": str(spec.get("author") or "").strip() or "AI 生成",
            "created_at": now, "updated_at": now, "icon": "",
            "source_ops": ops}
    try:
        d = plugin_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "source_ops.json").write_text(
            json.dumps(ops, ensure_ascii=False, indent=2), encoding="utf-8")
        if isinstance(copilot, dict) and copilot:
            (d / "copilot.json").write_text(
                json.dumps(copilot, ensure_ascii=False, indent=2), encoding="utf-8")
        icon_name = _write_icon_svg(d, spec.get("icon_svg"))
        if icon_name:
            meta["icon"] = icon_name
        _write_meta(name, meta)
    except OSError as e:
        return False, f"创建 Copilot 插件失败: {e}"

    rep(85, "插件校验系统检查中（静态规则 + LLM 源码评审）…")
    from zhuzhu_Copilot.core import plugin_guard
    gok, gmsg = plugin_guard.guard_source_package(d, name)
    if not gok:
        return False, gmsg
    _normalize_pkg_meta(name)
    n_files = len({str(op.get("path") or "") for op in ops if isinstance(op, dict)})
    ext = _manifest_summary(copilot if isinstance(copilot, dict) else {})
    msg = (f"已创建 Copilot 插件「{name}」（{n_files} 个插件文件、{len(ops)} 条变更），"
           "已通过插件校验系统。")
    if any(ext.values()):
        msg += ("\n应用扩展：" +
                "、".join(f"{k}×{v}" for k, v in ext.items() if v))
    msg += f"\n变更清单：{d / 'source_ops.json'}"
    # 创建即生效：自动写入转译层（与「启用」按钮同一套逻辑）
    rep(92, "正在写入转译层…")
    eok, emsg = set_enabled(name, True)
    if not eok:
        failed = _read_meta(name) or meta
        failed["enabled"] = False
        _write_meta(name, failed)
        return False, f"{msg}\n自动启用失败：{emsg}（插件已保持停用，可排查后手动启用）"
    msg += ("\n变更已即时热加载生效（无需重启；不修改应用源码文件；逐文件备份、"
            "编译检查，可 revert_copilot_plugin 即时回滚）")
    return True, msg
