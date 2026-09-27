"""文件隔离区：恶意文件移动隔离（记录元数据 + 哈希），支持按需恢复。

- 隔离目录根从配置读取（quarantine.root），文件与元数据分目录存储
- 隔离前计算 sha256，元数据记录原路径/时间/原因，误判可恢复
- 移动失败（文件占用/权限）不删除原文件，返回 False 交由上层决定
"""
import json
import os
import shutil
import time
from pathlib import Path
from typing import List, Optional

from winapp_migrator.core.security_engine.config import config
from winapp_migrator.core.security_engine.file_intel import sha256_of


class Quarantine:
    """文件隔离区管理器"""

    def __init__(self):
        self.root = config.path("quarantine.root", "%ProgramData%/WinAppMigrator/Quarantine")
        self.files_dir = self.root / "files"
        self.meta_dir = self.root / "meta"

    def isolate(self, path: str, reason: str = "") -> bool:
        """将文件移动到隔离区并记录元数据；成功返回 True"""
        src = Path(path)
        if not src.is_file():
            return False
        ident = f"{int(time.time() * 1000)}_{src.name}"
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        dst = self.files_dir / ident
        try:
            shutil.move(str(src), str(dst))
        except OSError:
            return False
        meta = {
            "id": ident,
            "name": src.name,
            "original_path": str(src),
            "sha256": sha256_of(str(dst)) or "",
            "reason": reason,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            (self.meta_dir / f"{ident}.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        return True

    def restore(self, ident: str) -> bool:
        """按 id 恢复文件到原路径；原位置已存在时追加 .restored 后缀避免覆盖"""
        meta = self._meta(ident)
        if not meta:
            return False
        src = self.files_dir / ident
        dst = Path(meta.get("original_path") or src.name)
        if not src.is_file():
            return False
        if dst.exists():
            dst = dst.with_name(dst.name + ".restored")
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            (self.meta_dir / f"{ident}.json").unlink(missing_ok=True)
            return True
        except OSError:
            return False

    def _meta(self, ident: str) -> Optional[dict]:
        f = self.meta_dir / f"{ident}.json"
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def list_items(self) -> List[dict]:
        """列出隔离区全部条目（元数据 + 是否可恢复）"""
        items = []
        if self.meta_dir.is_dir():
            for f in sorted(self.meta_dir.glob("*.json")):
                meta = self._meta(f.stem)
                if not meta:
                    continue
                meta["restorable"] = (self.files_dir / meta.get("id", "")).is_file()
                items.append(meta)
        return items


quarantine = Quarantine()
