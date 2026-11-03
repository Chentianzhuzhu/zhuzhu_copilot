"""Agent 头像：统一落盘 + 相对引用。

设计约束：
- **存引用不存绝对路径**：配置里只记文件名，文件落在 ``<data_root>/avatars/<kind>/``。
  数据目录整体搬迁（换机器 / 改安装路径）后头像仍然可用；同时杜绝把外部路径
  写进配置后被误当成本地头像反复读取。
- **大小与格式白名单**：只接受 PNG/JPG/JPEG/WEBP/BMP，单文件 ≤ ``MAX_AVATAR_BYTES``。
  头像要被打进每次聊天渲染，几十 MB 的原图会直接拖慢首屏，故在入口拦掉。
- **kind 隔离**：``agent``（自定义 Agent）与 ``group``（群聊成员）分目录存放，
  同名成员/Agent 互不覆盖；把群聊成员保存为 Agent 时用 :func:`copy_to` 复制引用。

引用格式：``<uuid>.<ext>``（不含任何路径分隔符），``resolve()`` 只接受文件名，
天然免疫目录穿越。
"""

import shutil
import uuid
from pathlib import Path

from zhuzhu_Copilot import app_identity

#: 允许的头像扩展名（小写比较）
AVATAR_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
#: 单个头像文件上限（字节）：2MB 足够 256px 头像，再大只是白占内存
MAX_AVATAR_BYTES = 2 * 1024 * 1024
#: 头像种类：自定义 Agent / 群聊成员
KINDS = ("agent", "group")


def avatars_root(kind: str = "agent") -> Path:
    """头像目录（不存在则创建）。未知 kind 归入 agent，避免拼出任意目录。"""
    k = kind if kind in KINDS else "agent"
    root = app_identity.data_root() / "avatars" / k
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return root


def valid_ext(name: str) -> bool:
    return Path(str(name or "")).suffix.lower() in AVATAR_EXTS


def store(src: str, kind: str = "agent") -> tuple:
    """把本地图片复制进头像目录。返回 (ok, 引用, 提示)。

    引用是 uuid 文件名：调用方无需先知道 Agent 名称，改名也不会产生孤儿旧图
    （旧图由调用方在替换/删除时用 :func:`remove` 回收）。
    """
    p = Path(str(src or "").strip())
    if not p.is_file():
        return False, "", "图片文件不存在"
    if not valid_ext(p.name):
        return False, "", "仅支持 PNG / JPG / WEBP / BMP"
    try:
        size = p.stat().st_size
    except OSError:
        return False, "", "无法读取图片"
    if size <= 0:
        return False, "", "图片为空文件"
    if size > MAX_AVATAR_BYTES:
        return False, "", "图片超过 %d MB，请先压缩" % (MAX_AVATAR_BYTES // (1024 * 1024))
    ref = "%s%s" % (uuid.uuid4().hex, p.suffix.lower())
    try:
        avatars_root(kind).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, avatars_root(kind) / ref)
        return True, ref, ""
    except OSError as e:
        return False, "", "保存失败: %s" % e


def resolve(ref: str, kind: str = "agent") -> str:
    """引用 → 绝对路径；非法引用或文件不存在返回空串。"""
    name = Path(str(ref or "").strip()).name
    if not name or not valid_ext(name):
        return ""
    p = avatars_root(kind) / name
    return str(p) if p.is_file() else ""


def remove(ref: str, kind: str = "agent") -> bool:
    """删除头像文件（引用为空或已不存在时静默返回 False）。"""
    path = resolve(ref, kind)
    if not path:
        return False
    try:
        Path(path).unlink(missing_ok=True)
        return True
    except OSError:
        return False


def gc(kind: str, used_refs) -> int:
    """回收未被任何配置引用的头像文件，返回删除数量。

    换图 / 删除成员 / 编辑弹窗中途取消都会留下孤儿文件。调用方在**保存成功后**
    传入当前全部有效引用做一次全量对账 —— 只有在「引用集合完整」时才能删，
    否则会误删别的配置正在用的图。
    """
    keep = {Path(str(x).strip()).name for x in (used_refs or []) if str(x).strip()}
    removed = 0
    try:
        files = list(avatars_root(kind).glob("*"))
    except OSError:
        return 0
    for p in files:
        if not p.is_file() or p.name in keep:
            continue
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def copy_to(ref: str, src_kind: str, dst_kind: str) -> str:
    """把一个 kind 下的头像复制到另一个 kind（群聊成员 → 可复用 Agent）。

    返回新引用；源引用为空或文件缺失时返回空串（调用方按「无头像」处理）。
    """
    path = resolve(ref, src_kind)
    if not path:
        return ""
    ok, new_ref, _msg = store(path, dst_kind)
    return new_ref if ok else ""
