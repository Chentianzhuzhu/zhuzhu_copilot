"""源码变更引擎：把插件包的变更清单（source_ops）写入应用**转译层**

面向**Copilot 插件**（应用级插件；Agent 插件不涉及应用源码，见 agent_plugins）：
开发者基于源码开发，插件包内 source_ops.json（或 plugin.json 的 "source_ops"
字段）描述对应用源码的变更，支持五类操作：

  create_file     新建功能模块（创建新模块）           {op, path, content}
  append          追加到现有代码模块之后               {op, path, content, after?}
                  after 缺省 = 文件末尾；给出锚点串则插到该锚点所在行之后
  insert_after    在锚点之后插入                       {op, path, anchor, content}
  replace_module  替换模块（start..end 两行之间，含边界）{op, path, start, end?, content}
                  end 缺省 = 替换到文件末尾
  replace_file    整体替换文件                         {op, path, content}

一个插件可携带多条 op、跨多个文件——即「源码级插入到任何源代码文件、新建源码
文件、一次改多个功能模块并集成功能」。

安全约束（与 plugin_guard 共同构成防线，缺一不可）：
  · 应用前必须通过 plugin_guard.guard_source_package（静态规则 + LLM 源码评审），
    危险即整体删除插件包并提示用户；
  · 路径只允许应用源码根目录内的相对路径：禁绝对路径、禁 .. 穿越；
  · PROTECTED_CORE_FILES（插件校验系统自身与安全引擎）不可被任何源码插件改动；
  · 每个被改文件先备份到插件目录 backups/，全部应用后逐文件 py_compile，
    任一编译失败 → 依据备份整体回滚（创建的删除、修改的还原）；
  · applied.json 记录已应用的变更清单，revert 时据此卸载回滚；
    卸载时文件已被用户改动的（哈希不符）跳过该文件并注明，不覆盖用户劳动成果。

**写入目标固定为转译层（与运行形态无关）**：源码运行与 exe 打包运行都不改应用
源码文件，变更统一写入用户数据目录的「转译层」
~/.zhuzhu_Copilot/source_overlay/（目录结构镜像源码相对路径），由
src/_copilot_overlay_hook.py 的导入钩子让 zhuzhu_Copilot.* 的导入优先命中转译层
——源码级插件因此可以改主题、自定义 Agent、自定义工具、自定义语言、增删替换任何
模块，安装版与源码版行为一致（写入后由 core/hot_reload.py 即时热加载，无需重启）。

对外入口（包目录级，Copilot 插件使用）：
  load_source_ops_from(pkg_dir)      读取插件包的变更清单
  apply_source_ops(pkg_dir, name)    应用变更（校验→备份→应用→编译→记录）
  revert_source_ops(pkg_dir, name)   按 applied.json 卸载回滚
  source_ops_status(pkg_dir)         应用状态
"""

import hashlib
import json
import re
import sys
import time
from pathlib import Path

from zhuzhu_Copilot.core import plugin_guard

MAX_OPS = 50                # 单插件 op 数上限（防病态包）
MAX_CONTENT_CHARS = 512000  # 单 op 内容上限（512KB）

#: 每种 op 的**必需**字段（寻址字段 anchor/start 与 line/start_line 二选一，
#: 由 _check_op 单独校验，见 _OP_ADDRESS）
_OP_FIELDS = {
    "create_file": ("path", "content"),
    "replace_file": ("path", "content"),
    "append": ("path", "content"),
    "insert_after": ("path", "content"),      # 寻址：anchor 或 line
    "insert_at": ("path", "line", "content"),  # 在第 line 行**之前**插入（1 起）
    "replace_lines": ("path", "start_line", "content"),   # 替换第 start..end_line 行
    "replace_module": ("path", "content"),     # 寻址：start/end 或 start_line/end_line
}

#: 寻址方式要求：op -> (文本锚点字段组, 行号字段组)，任一组齐全即可
_OP_ADDRESS = {
    "insert_after": (("anchor",), ("line",)),
    "insert_at": ((), ("line",)),
    "replace_lines": ((), ("start_line",)),
    "replace_module": (("start",), ("start_line",)),
}


def app_source_root():
    """应用原版源码根目录（src/，仅用于读取原版内容作基线）；打包形态返回 None。"""
    if getattr(sys, "frozen", False):
        return None
    return Path(__file__).resolve().parents[2]


def overlay_root() -> Path:
    """转译层根目录（所有源码级插件变更的唯一落地处）。

    与 src/_copilot_overlay_hook.py 的 overlay_dir() 同源；该模块在导入链最前端
    （main.py 里先于包导入安装钩子），此处经 import 取真值，失败时回落同规则计算。
    """
    try:
        import _copilot_overlay_hook
        return _copilot_overlay_hook.overlay_dir()
    except Exception:
        from zhuzhu_Copilot import app_identity
        return app_identity.data_root() / "source_overlay"


def apply_root() -> tuple:
    """源码级插件变更的写入根与模式：**始终为转译层**（overlay）。

    无论是源码运行还是 exe 打包运行，都不直接写入应用源码文件：
      · 打包安装目录通常不可写（Program Files），直写源码必然失败；
      · 源码运行直写会让插件变更与仓库工作区纠缠，回滚/升级都容易出错。
    统一写入用户数据目录的转译层后，两种形态行为一致，导入钩子优先加载覆盖层，
    回滚只需清理转译层（详见 _copilot_overlay_hook.py）。
    """
    return overlay_root(), "overlay"


def _packaged_file(rel: str):
    """打包/源码树中某相对路径的原文件（覆盖层模式下「当前内容」的来源）。"""
    try:
        import _copilot_overlay_hook
        p = _copilot_overlay_hook._original_file(str(rel).replace("\\", "/").split("/"))
        return p if (p is not None and p.is_file()) else None
    except Exception:
        return None


# ------------------------------------------------------------
# 插件包（pkg_dir）读写：元数据 / 变更清单
# ------------------------------------------------------------

def _pkg_meta(pkg_dir: Path) -> dict:
    mf = Path(pkg_dir) / "plugin.json"
    if not mf.is_file():
        return {}
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _pkg_write_meta(pkg_dir: Path, meta: dict) -> bool:
    try:
        (Path(pkg_dir) / "plugin.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False


def load_source_ops_from(pkg_dir) -> list:
    """从插件包目录读取源码变更清单：优先 source_ops.json，回退 plugin.json 的
    source_ops 字段。文件缺失/损坏返回 []。"""
    d = Path(pkg_dir)
    meta = _pkg_meta(d)
    ops = meta.get("source_ops")
    if not ops:
        f = d / "source_ops.json"
        if f.is_file():
            try:
                ops = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                ops = None
    if not isinstance(ops, list):
        return []
    return [op for op in ops if isinstance(op, dict)]


# ------------------------------------------------------------
# op 应用
# ------------------------------------------------------------

def _resolve_target(root: Path, rel) -> Path:
    """把 op 里的 path 解析为源码树内的绝对路径（拒绝绝对路径/穿越/受保护文件）。"""
    rel = str(rel or "").replace("\\", "/").strip()
    if not rel:
        raise ValueError("op 缺少 path")
    if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        raise ValueError(f"path 必须是相对路径：{rel}")
    if ".." in rel.split("/"):
        raise ValueError(f"path 不允许上级目录穿越：{rel}")
    p = (root / rel).resolve()
    root_r = root.resolve()
    if p != root_r and root_r not in p.parents:
        raise ValueError(f"path 越出源码根目录：{rel}")
    if plugin_guard.is_protected_path(rel):
        raise ValueError(f"path 指向受保护的核心程序，禁止改动：{rel}")
    return p


def _find_line(lines: list, anchor: str, unique: bool):
    """返回锚点串所在行号（unique=True 要求恰好一行命中）。"""
    hits = [i for i, ln in enumerate(lines) if anchor in ln]
    if not hits:
        raise ValueError(f"找不到锚点：{anchor[:60]}")
    if unique and len(hits) > 1:
        raise ValueError(f"锚点不唯一（{len(hits)} 处命中）：{anchor[:60]}")
    return hits[-1]


def _present(v) -> bool:
    """字段是否存在（0 是合法行号，不能被 `v or ''` 当成缺失）。"""
    return v is not None and str(v).strip() != ""


def _line_no(op: dict, key: str, default=None) -> int:
    """读取 1 起的行号字段（允许数字字符串）；非法/越界由调用方判定。"""
    raw = op.get(key, default)
    if raw is None or str(raw).strip() == "":
        raise ValueError(f"op {op.get('op')} 缺少行号字段：{key}")
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError(f"op {op.get('op')} 的 {key} 必须是整数行号：{raw!r}")
    if n < 1:
        raise ValueError(f"op {op.get('op')} 的 {key} 必须 ≥1（行号从 1 开始）：{n}")
    return n


def _check_op(op: dict, idx: int = 0) -> tuple:
    """校验单条 op 的结构与寻址字段。返回 (ok, msg)。

    寻址两种方式（同一 op 二选一，行号优先）：
      · 文本锚点：anchor / start（要求目标文件里真实存在且唯一）
      · 行号    ：line / start_line..end_line（1 起，含边界）
    行号方式适合「拿得到完整源码（如 GitHub 仓库）」的插件作者精确改指定行。
    """
    kind = str(op.get("op") or "").strip()
    tag = f"第 {idx + 1} 条 op（{kind or '缺失'}）"
    if kind not in _OP_FIELDS:
        return False, f"第 {idx + 1} 条 op 类型未知：{kind or '(缺失)'}"
    missing = [f for f in _OP_FIELDS[kind] if not _present(op.get(f))]
    if missing:
        return False, f"{tag}缺少字段：{', '.join(missing)}"
    need = _OP_ADDRESS.get(kind)
    if need:
        text_fields, line_fields = need
        has_text = all(_present(op.get(f)) for f in text_fields) if text_fields else False
        has_line = all(_present(op.get(f)) for f in line_fields)
        if not has_text and not has_line:
            want = " 或 ".join(
                x for x in ("/".join(text_fields) if text_fields else "",
                            "/".join(line_fields)) if x)
            return False, f"{tag}缺少寻址字段（{want}）"
        if has_line:
            try:
                start = _line_no(op, line_fields[0])
                if "end_line" in op and _present(op.get("end_line")):
                    end = _line_no(op, "end_line")
                    if end < start:
                        return False, f"{tag}的 end_line（{end}）小于 start_line（{start}）"
            except ValueError as e:
                return False, f"{tag}{e}"
    return True, ""


def _write_keep_eol(target: Path, text: str, before: str) -> None:
    """按原文件的换行风格写回（CRLF 文件不被整篇改成 LF，避免全文件 diff）。"""
    eol = "\r\n" if "\r\n" in before else "\n"
    if eol != "\n":
        text = text.replace("\r\n", "\n").replace("\n", eol)
    if before.endswith(("\n", "\r\n")) and not text.endswith(("\n", "\r\n")):
        text += eol
    target.write_text(text, encoding="utf-8", newline="")


def _apply_op(root: Path, op: dict, orig_root=None) -> dict:
    """应用单条 op，返回 {path, action, before}。before = 原文本（新建为 None）。

    寻址支持两种（行号优先，其次文本锚点）：
      · 行号：line / start_line..end_line（1 起，含边界）—— 插件作者拿得到完整源码
        （如 GitHub 仓库）时可按行精确落位；
      · 锚点：anchor / start（要求目标文件里真实存在且唯一）—— 对版本差异更鲁棒。

    orig_root：覆盖层模式下原版源码的查找根（打包内/源码目录）。目标文件在
    root（覆盖层）里还不存在时，先把原版复制进来再改——保证「以当前生效的
    版本为基准」做追加/插入/替换，且备份里存的是原版内容。
    """
    ok, why = _check_op(op, 0)
    if not ok:
        raise ValueError(why)
    kind = str(op.get("op") or "").strip()
    content = str(op.get("content") or "")
    if len(content) > MAX_CONTENT_CHARS:
        raise ValueError(f"op {kind} 内容超过 {MAX_CONTENT_CHARS} 字符上限")
    target = _resolve_target(root, op.get("path"))
    rel = str(target.relative_to(root))

    if kind == "create_file":
        if target.exists():
            raise ValueError(f"文件已存在，create_file 拒绝覆盖：{op.get('path')}"
                             "（如需整体替换请用 replace_file）")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": rel, "action": "created", "before": None}

    if not target.is_file():
        # 覆盖层模式：把原版复制进覆盖层，再在副本上做变更
        orig = None
        if orig_root is not None:
            cand = Path(orig_root) / rel
            if cand.is_file():
                orig = cand
        if orig is None:
            orig = _packaged_file(rel)
        if orig is None:
            raise ValueError(f"目标文件不存在：{op.get('path')}")
        target.parent.mkdir(parents=True, exist_ok=True)
        # 用字节读写：text 模式的通用换行会把 CRLF 吞成 LF，EOL 判定与备份都会失真
        target.write_bytes(orig.read_bytes())
    before = target.read_bytes().decode("utf-8", errors="replace")
    lines = before.splitlines()
    n = len(lines)

    if kind == "replace_file":
        after = content
    elif kind == "append":
        idx = _find_line(lines, str(op["after"]), unique=False) \
            if str(op.get("after") or "").strip() else n - 1
        after = "\n".join(lines[:idx + 1] + [""] + content.splitlines()
                          + lines[idx + 1:])
    elif kind == "insert_at":
        # 在第 line 行**之前**插入；line = n+1 等价于追加到文件末尾
        idx = _line_no(op, "line") - 1
        if idx > n:
            raise ValueError(f"insert_at 行号越界：line={idx + 1} > 文件行数 {n}"
                             f"（追加到末尾请用 line={n + 1}）")
        after = "\n".join(lines[:idx] + content.splitlines() + lines[idx:])
    elif kind == "insert_after":
        if _present(op.get("line")):
            idx = _line_no(op, "line") - 1
            if idx >= n:
                raise ValueError(f"insert_after 行号越界：line={idx + 1} > 文件行数 {n}")
            after = "\n".join(lines[:idx + 1] + [""] + content.splitlines()
                              + lines[idx + 1:])
        else:
            idx = _find_line(lines, str(op["anchor"]), unique=True)
            after = "\n".join(lines[:idx + 1] + [""] + content.splitlines()
                              + lines[idx + 1:])
    elif kind == "replace_lines":
        s0 = _line_no(op, "start_line") - 1
        e0 = (_line_no(op, "end_line") - 1
              if _present(op.get("end_line")) else s0)
        if s0 >= n:
            raise ValueError(f"replace_lines 起始行越界：start_line={s0 + 1} > 文件行数 {n}")
        if e0 >= n:
            raise ValueError(f"replace_lines 结束行越界：end_line={e0 + 1} > 文件行数 {n}")
        if e0 < s0:
            raise ValueError("replace_lines 的 end_line 小于 start_line")
        after = "\n".join(lines[:s0] + content.splitlines() + lines[e0 + 1:])
    else:   # replace_module（锚点 或 行号区间）
        if _present(op.get("start_line")):
            s0 = _line_no(op, "start_line") - 1
            e0 = (_line_no(op, "end_line") - 1
                  if _present(op.get("end_line")) else n - 1)
            if s0 >= n:
                raise ValueError(f"replace_module 起始行越界：start_line={s0 + 1} > 文件行数 {n}")
            if e0 >= n:
                e0 = n - 1
            if e0 < s0:
                raise ValueError("replace_module 的 end_line 小于 start_line")
        else:
            s0 = _find_line(lines, str(op["start"]), unique=True)
            end_a = str(op.get("end") or "").strip()
            e0 = _find_line(lines, end_a, unique=True) if end_a else n - 1
            if e0 < s0:
                raise ValueError(f"模块结束锚点在起始锚点之前：{op.get('path')}")
        after = "\n".join(lines[:s0] + content.splitlines() + lines[e0 + 1:])

    _write_keep_eol(target, after, before)
    return {"path": rel, "action": "modified", "before": before}


def _py_compile_ok(path: Path) -> tuple:
    """编译单个 .py 文件；非 .py 直接通过。返回 (ok, 错误信息)。"""
    if path.suffix.lower() != ".py":
        return True, ""
    import py_compile
    try:
        py_compile.compile(str(path), doraise=True)
        return True, ""
    except py_compile.PyCompileError as e:
        return False, str(e)[:500]
    except Exception as e:
        return False, str(e)[:500]


def _backup_path(pkg_dir, rel: str) -> Path:
    flat = rel.replace("/", "__").replace("\\", "__")
    return Path(pkg_dir) / "backups" / (flat + ".bak")


def validate_source_ops(ops: list) -> tuple:
    """应用前静态校验 op 结构（不含内容安全——内容安全归 plugin_guard）。"""
    if not ops:
        return False, "源码变更清单为空（source_ops）"
    if len(ops) > MAX_OPS:
        return False, f"源码变更清单超过 {MAX_OPS} 条上限"
    for i, op in enumerate(ops):
        ok, why = _check_op(op, i)
        if not ok:
            return False, why
    return True, ""


# ------------------------------------------------------------
# 通用：应用 / 回滚 / 状态（包目录级，Agent 与 Copilot 插件共用）
# ------------------------------------------------------------

def apply_source_ops(pkg_dir, name: str, ops: list | None = None) -> tuple:
    """把源码变更清单应用到插件源码层（包目录级通用入口）。

    流程：结构校验 → **插件校验系统**（危险即整体删除插件包并提示）→ 逐文件
    备份 → 逐条应用 → 全部 py_compile（任一失败整体回滚）→ 写 applied.json
    清单。返回 (ok, message)。

    写入目标固定为转译层（用户数据目录下的源码覆盖层）：打包版与源码版一致，
    都不直接改写应用源码文件；导入钩子优先加载覆盖层，写入后由热加载引擎
    （core/hot_reload.py）即时装载，无需重启应用。
    """
    root, mode = apply_root()
    orig_root = app_source_root()   # 打包态为 None → _apply_op 回落打包内源码快照
    d = Path(pkg_dir)
    meta = _pkg_meta(d)
    if not d.is_dir():
        return False, f"插件「{name}」不存在"
    if (d / "applied.json").is_file():
        return True, f"插件「{name}」的变更已在转译层，无需重复写入"
    if ops is None:
        ops = load_source_ops_from(d)
    ok, why = validate_source_ops(ops)
    if not ok:
        return False, why

    # 1. 插件校验系统（内置唯一、不可替换）：危险即整体删除插件包并提示
    gok, gmsg = plugin_guard.guard_source_package(d, name)
    if not gok:
        return False, gmsg

    # 2. 逐条应用（内存中保留原文，失败可整体回滚）
    done = []          # [{path, action, before, backup}]
    try:
        for op in ops:
            rec = _apply_op(root, op, orig_root=orig_root)
            rel = rec["path"]
            bak = _backup_path(d, rel)
            # 备份必须是**应用前的原始内容**：同一文件被多条 op 改时，
            # 只有第一次（before=原文）写盘，后续 op 不覆盖（否则 revert
            # 只能还原到中间态）。进程内整体回滚用各自 before，不受影响。
            if rec["action"] == "modified" and not bak.exists():
                bak.parent.mkdir(parents=True, exist_ok=True)
                bak.write_bytes(rec["before"].encode("utf-8"))
            done.append({**rec, "backup": str(bak)})
    except (ValueError, OSError) as e:
        _rollback(root, done)
        return False, f"应用源码变更失败（已整体回滚，源码未被修改）：{e}"

    # 3. 全部应用后编译检查：任一 .py 编译失败 → 整体回滚
    for rec in done:
        target = root / rec["path"]
        cok, cerr = _py_compile_ok(target)
        if not cok:
            _rollback(root, done)
            return False, (f"编译检查未通过（已整体回滚）：{rec['path']}\n{cerr}")

    # 4. 记录已应用清单（供 revert 卸载回滚）。
    # sha_after = 应用后的文件哈希：revert 时据此判断文件在应用后是否被用户
    # 手动改过（改了就不还原/不删除，不覆盖用户劳动成果）。
    files_mf = []
    for r in done:
        try:
            sha_after = hashlib.sha256((root / r["path"]).read_bytes()).hexdigest()
        except OSError:
            sha_after = ""
        files_mf.append({"path": r["path"], "action": r["action"],
                         "backup": r["backup"], "sha_after": sha_after})
    manifest = {"applied_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "plugin": name, "ops": len(ops), "files": files_mf,
                "mode": mode}
    try:
        (d / "applied.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        meta["source_applied"] = True
        meta["source_applied_at"] = manifest["applied_at"]
        meta["source_mode"] = mode
        meta["updated_at"] = manifest["applied_at"]
        _pkg_write_meta(d, meta)
    except OSError as e:
        _rollback(root, done)
        return False, f"记录应用清单失败（已整体回滚）：{e}"

    lines = [f"源码插件「{name}」已应用：{len(ops)} 条变更、{len(done)} 个文件。"]
    lines += [f"  · {r['action']} {r['path']}" for r in done]
    lines.append(f"变更已写入转译层：{root}")
    lines.append("变更已写入转译层；由热加载引擎（core/hot_reload.py）即时装载，"
                 "无需重启应用（应用源码文件保持不变）。")
    return True, "\n".join(lines)


def _rollback(root: Path, done: list) -> None:
    """依据内存记录整体回滚：创建的删除、修改的还原。"""
    for rec in reversed(done):
        target = root / rec["path"]
        try:
            if rec["action"] == "created":
                if target.is_file():
                    target.unlink()
            elif rec.get("before") is not None:
                target.write_bytes(rec["before"].encode("utf-8"))
        except OSError:
            pass


def revert_source_ops(pkg_dir, name: str) -> tuple:
    """卸载回滚：按 applied.json 还原（创建的文件删除、修改的文件还原备份）。

    root = 转译层（与写入时一致）。写入后已被用户手动改过的文件（内容哈希与记录
    不符）跳过并注明，不覆盖。
    """
    root, _mode = apply_root()
    d = Path(pkg_dir)
    mf = d / "applied.json"
    if not mf.is_file():
        return True, f"插件「{name}」没有已写入的变更，无需回滚"
    try:
        manifest = json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, "applied.json 损坏，无法安全回滚（请手动按 backups/ 还原）"
    restored, removed, skipped = [], [], []
    for rec in reversed(manifest.get("files") or []):
        target = root / str(rec.get("path") or "")
        try:
            if not target.is_file():
                if rec.get("action") == "created":
                    continue          # 新建文件已不在（如被用户删了），无需处理
                skipped.append(f"{rec['path']}（文件已不存在）")
                continue
            cur = hashlib.sha256(target.read_bytes()).hexdigest()
            if cur != str(rec.get("sha_after") or ""):
                # 应用后被用户手动改过：不覆盖/不删除用户劳动成果，交还原说明
                skipped.append(f"{rec['path']}（应用后已被修改，未覆盖）")
                continue
            if rec.get("action") == "created":
                target.unlink()
                removed.append(rec["path"])
                continue
            bak = Path(str(rec.get("backup") or ""))
            if not bak.is_file():
                skipped.append(f"{rec['path']}（缺少备份）")
                continue
            restored_bytes = bak.read_bytes()
            # 还原内容若与应用原版一致，直接删掉转译层副本，让模块回落原版
            # （转译层不留残骸）；不一致（此前被其他插件改过）才把备份内容写回去。
            orig = _packaged_file(rec["path"])
            if orig is not None and orig.read_bytes() == restored_bytes:
                target.unlink()
                restored.append(rec["path"])
                continue
            target.write_bytes(restored_bytes)
            restored.append(rec["path"])
        except OSError as e:
            skipped.append(f"{rec['path']}（{e}）")
    try:
        mf.unlink()
        meta = _pkg_meta(d)
        meta.pop("source_applied", None)
        meta.pop("source_applied_at", None)
        meta["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _pkg_write_meta(d, meta)
        # 清掉本次备份：下次 apply 时备份必须来自「应用前」的当前内容
        import shutil
        shutil.rmtree(d / "backups", ignore_errors=True)
    except OSError:
        pass
    parts = [f"源码插件「{name}」已卸载回滚。"]
    if removed:
        parts.append("已删除新建文件：" + "、".join(removed))
    if restored:
        parts.append("已还原修改文件：" + "、".join(restored))
    if skipped:
        parts.append("跳过：" + "；".join(skipped))
    return True, "\n".join(parts)


def source_ops_status(pkg_dir) -> dict:
    """源码变更应用状态：{applied, applied_at, ops, files}（files 去重、保序）。"""
    d = Path(pkg_dir)
    mf = d / "applied.json"
    if not mf.is_file():
        return {"applied": False, "applied_at": "", "ops": 0, "files": []}
    try:
        m = json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"applied": False, "applied_at": "", "ops": 0, "files": []}
    files = []
    for f in (m.get("files") or []):
        p = str(f.get("path") or "")
        if p and p not in files:
            files.append(p)
    return {"applied": True,
            "applied_at": str(m.get("applied_at") or ""),
            "ops": int(m.get("ops") or 0),
            "files": files}
