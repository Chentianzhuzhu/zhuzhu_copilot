---
name: system-admin
description: 系统管理：find_app 定位应用、system_info/get_time/env_var 查系统信息、optimize_memory 清理内存、uninstall_app 卸载、migrate_app 迁移应用
---

# system-admin：系统信息查询与应用管理

当需要查询系统信息、定位应用、清理内存、卸载或迁移应用时使用本技能。

## 1. 查询类（无副作用）
- system_info：主机名/系统版本/CPU 核心数/物理内存
- get_time：当前时间
- env_var(name)：读取环境变量

## 2. 定位应用
find_app(query=应用名)：秒查已安装应用的可启动路径，比逐层截图找图标高效。

## 3. 重量级操作（工具内部会弹用户确认）
- optimize_memory：清理内存（终止可安全退出的后台进程、压缩工作集）
- uninstall_app(name)：卸载应用（先调自带卸载器再清理残留）
- migrate_app(name, target)：把应用迁移到其他盘
先向用户说明影响，确认后再执行；完成后汇报结果。
