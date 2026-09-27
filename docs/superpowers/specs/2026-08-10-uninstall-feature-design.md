# 强力卸载功能设计

日期：2026-08-10

## 目标

在 WinAppMigrator 中加入"强力卸载"：删除应用根目录、注册表关联项、快捷方式、数据/存档/聊天数据目录。支持 Win32 与 UWP。

## 决策

- **UWP**：走 `Remove-AppxPackage`（用户已确认支持）
- **确认交互**：点击卸载后弹出删除清单（根目录、数据目录逐条、快捷方式数、注册表清理数），用户确认后才执行（用户已确认）
- **注册表安全**：Uninstall/App Paths 中匹配该 app 的子键整键删除；Run/RunOnce 匹配值删除；publisher 顶级键只删其中引用该 app 路径的键/值，**不删整个顶级键**（避免误伤同发布商其他应用）
- **不可恢复**：不保留任何备份

## 架构

```
ui/main_window.py     右侧卡片加"强力卸载"按钮 + 清单确认对话框 + UninstallWorker
core/uninstaller.py   新建：UninstallPlan / Uninstaller（build_plan + uninstall）
core/registry.py      新增 scan_app_entries / remove_app_entries
core/shortcut.py      新增 scan_shortcuts / remove_shortcuts
core/uwp.py           新增 uninstall_package（Remove-AppxPackage）
```

## 数据流

1. 用户选中 app，点"强力卸载"
2. `Uninstaller.build_plan(app)`：
   - UWP → 记录包全名，不扫描数据目录
   - Win32 → `detect_data_dirs(app)` 数据目录 + `scan_app_entries` 注册表位置 + `scan_shortcuts` 快捷方式
3. 确认对话框列出清单
4. `Uninstaller.uninstall(app, plan, callback)`：
   - UWP → Remove-AppxPackage
   - Win32 → 强制结束占用进程 → 删快捷方式 → 删注册表 → 删数据目录 → 删根目录
5. 分阶段进度上报（复用 progress bar + 日志）

## 错误处理

- 每步失败不中断后续；失败项记入日志并在结果中提示
- 目录删除复用 `safe_remove`（只读属性 + 重试）
- 注册表删除失败记录日志（`KEY_READ | KEY_WRITE | KEY_WOW64_64KEY` 权限）

## 测试

- 构造临时目录验证 build_plan 清单与删除流程
- UWP 路径代码审查 + 弹窗确认，不实际卸载系统包
