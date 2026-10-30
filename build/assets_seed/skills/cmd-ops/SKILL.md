---
name: cmd-ops
description: 命令执行与下载：run_command 执行命令（含白名单/沙盒约束）、check_command 轮询后台命令、fast_download 高速下载文件
---

# cmd-ops：命令执行、轮询与下载

当需要在终端执行命令、下载文件时使用本技能。

## 1. 执行命令
run_command(command=命令, wait=等待秒数默认5, force_quit=是否超时强杀)：
- 危险命令（删除/格式化/关机等）会被沙盒拒绝，先想清楚再执行
- 短命令：默认 wait=5 等输出；启动 GUI 应用建议 wait=1
- 长任务：wait 秒内未完成且 force_quit=false 会转入后台，返回命令 ID
- 预计长时间挂起/无输出的命令：force_quit=true 防阻塞

## 2. 轮询后台命令
check_command(cmd_id=命令ID) 查询进度与最新输出；不传 cmd_id 列出全部后台命令。

## 3. 下载文件
fast_download(url=下载地址, dest_dir=保存目录留空用工作目录)：多段并发高速下载，自动探测文件名，支持断点续传。

## 4. 汇报
输出：命令执行结果/退出码、下载的文件路径。
