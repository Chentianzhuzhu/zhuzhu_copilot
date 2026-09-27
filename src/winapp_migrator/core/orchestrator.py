import shutil
from pathlib import Path
from typing import Callable, List, Optional

from winapp_migrator.utils.helpers import setup_logging, safe_remove
from winapp_migrator.core.app_scanner import AppInfo
from winapp_migrator.core.migration import migrate_folder, _terminate_processes, is_360_self_protection
from winapp_migrator.core.registry import RegistryPathUpdater
from winapp_migrator.core.shortcut import update_shortcuts
from winapp_migrator.core.uwp import UWPManager

logger = setup_logging()

class MigrationOrchestrator:
    def __init__(self):
        self.registry_updater = RegistryPathUpdater()

    def migrate(
        self,
        app: AppInfo,
        target: Path,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        update_registry: bool = True,
        extra_dirs: Optional[List[Path]] = None,
        on_conflict: Optional[Callable[[Path, Path], bool]] = None,
    ) -> dict:
        source = app.install_location
        target.parent.mkdir(parents=True, exist_ok=True)

        logger.info("开始迁移 %s (%s): %s -> %s", app.name, app.app_type, source, target)

        if app.app_type == "UWP":
            blocked = _terminate_processes(source)
            success, message = UWPManager.migrate_package(
                app.package_name or app.name,
                Path(target.anchor),  # UWP 按盘符注册，取目标盘
                progress_callback,
            )
            return {
                "success": success,
                "message": message,
                "source": str(source),
                "target": str(target),
                "registry_changed": 0,
                "blocked": blocked,
                "blocked_360": is_360_self_protection(blocked),
            }

        # 1. 完全移动主目录 + 各数据目录
        self._notify(progress_callback, 5, "开始迁移...")
        moved: List[tuple[Path, Path]] = []
        all_blocked = _terminate_processes(source)
        if all_blocked:
            self._notify(progress_callback, 3, f"结束占用进程... 以下进程无法自动结束: {', '.join(all_blocked)}")
        result = migrate_folder(source, target, progress_callback, mode="move", on_conflict=on_conflict)
        if not result.success:
            return {
                "success": False,
                "message": result.message,
                "blocked": all_blocked,
                "blocked_360": is_360_self_protection(all_blocked),
            }
        moved.append((source, target))

        for extra in extra_dirs or []:
            # 数据目录跟随主目标位置：{目标路径}_Data\{数据目录名}
            extra_target = target.parent / (target.name + "_Data") / extra.name
            self._notify(progress_callback, None, f"迁移数据目录: {extra}")
            all_blocked += _terminate_processes(extra)
            r = migrate_folder(extra, extra_target, progress_callback, mode="move", on_conflict=on_conflict)
            if not r.success:
                self._rollback(moved)
                return {
                    "success": False,
                    "message": f"数据目录迁移失败: {extra}\n{r.message}",
                    "blocked": all_blocked,
                    "blocked_360": is_360_self_protection(all_blocked),
                }
            moved.append((extra, extra_target))

        # 2. 更新主安装目录的注册表 + 快捷方式引用（数据目录仅迁移文件）
        registry_changed, registry_errors = 0, 0
        shortcuts_changed = 0
        if update_registry:
            self._notify(progress_callback, None, "更新注册表路径引用...")
            # 用 publisher/可执行名定位 app 相关注册表键，避免全量扫描系统巨树
            hints = [app.publisher, Path(app.executable or "").stem, app.name]
            registry_changed, registry_errors = self.registry_updater.update_paths(source, target, hints)
        self._notify(progress_callback, None, "更新快捷方式...")
        shortcuts_changed += update_shortcuts(source, target)

        # 3. 清理旧目录备份
        for old, _new in moved:
            backup = Path(str(old) + ".migrator_backup")
            if backup.exists():
                if safe_remove(backup):
                    self._notify(progress_callback, None, f"已删除旧目录: {old}")
                else:
                    self._notify(progress_callback, None, f"旧目录备份保留: {backup}")

        self._notify(progress_callback, 100, "完成")
        return {
            "success": True,
            "message": result.message,
            "details": result.details,
            "source": str(source),
            "target": str(target),
            "registry_changed": registry_changed,
            "registry_errors": registry_errors,
            "shortcuts_changed": shortcuts_changed,
            "blocked": all_blocked,
            "blocked_360": is_360_self_protection(all_blocked),
        }

    def _rollback(self, moved: List[tuple[Path, Path]]):
        """回滚已迁移的目录：删除新位置，恢复旧位置"""
        for old, new in reversed(moved):
            try:
                shutil.rmtree(new, ignore_errors=True)
            except Exception:
                pass
            backup = Path(str(old) + ".migrator_backup")
            if backup.exists():
                try:
                    backup.rename(old)
                except Exception:
                    pass

    def _notify(self, callback, percent, message: str):
        logger.info("[%s%%] %s", percent, message)
        if callback:
            callback(percent or 0, message)
