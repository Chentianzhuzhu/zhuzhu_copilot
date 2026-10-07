"""头像处理：base64 → 落盘 → 返回可访问 URL。

原先这段逻辑内联在``api/auth.py`` 的注册接口里，管理后台建号也要用同样的一套
校验（大小/ 格式 / 落盘 / URL 生成），故抽为共享服务，避免两处实现漂移。
"""
import base64
import binascii
import io
import os
import uuid

from PIL import Image

#: 头像存储目录（相对项目根，与 static 挂载点对应）
AVATAR_DIR = os.path.join("static", "uploads", "avatars")
MAX_AVATAR_SIZE = 2 * 1024 * 1024  # 2MB
ALLOWED_FORMATS = {"JPEG": "jpg", "PNG": "png", "GIF": "gif", "WEBP": "webp"}


class AvatarError(ValueError):
    """头像非法（大小超限 / 格式不支持 / 解码失败）。"""


def validate_image_header(file_bytes: bytes) -> bool:
    """用 Pillow 验证图片文件头是否合法。"""
    try:
        Image.open(io.BytesIO(file_bytes)).verify()
        return True
    except Exception:
        return False


def save_avatar_base64(avatar_b64: str) -> str:
    """把 base64（可带 dataURL 前缀）头像存盘，返回形如
    ``/static/uploads/avatars/<uuid>.png`` 的 URL。

    失败抛 :class:`AvatarError`（消息可直接展示给管理员）。
    """
    if not avatar_b64:
        return ""
    raw = avatar_b64
    if "," in raw:                      # 去掉 dataURL 前缀
        raw = raw.split(",", 1)[1]
    try:
        img_bytes = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise AvatarError("头像数据不是合法的 base64")

    if len(img_bytes) > MAX_AVATAR_SIZE:
        raise AvatarError("头像大小不能超过2MB")
    if not validate_image_header(img_bytes):
        raise AvatarError("头像文件格式不合法")

    try:
        fmt = Image.open(io.BytesIO(img_bytes)).format or ""
    except Exception:
        raise AvatarError("头像文件格式不合法")
    ext = ALLOWED_FORMATS.get(fmt.upper())
    if not ext:
        raise AvatarError("头像仅支持 jpg/png/gif/webp 格式")

    os.makedirs(AVATAR_DIR, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(AVATAR_DIR, filename), "wb") as f:
        f.write(img_bytes)
    return f"/static/uploads/avatars/{filename}"