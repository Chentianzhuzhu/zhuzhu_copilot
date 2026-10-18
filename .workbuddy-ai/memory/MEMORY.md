# 项目长期记忆

## 服务器资产
- **Redis 缓存节点**：`122.228.216.138:6379`，Debian 12 / 宝塔面板，Redis 7.0.15，2026-10-19 部署。密码与配置见 `outputs/redis_deployment_report.md`。用于官网 + 客户端 API 高并发缓存。
- **业务域 ACL 账号**：api_order / api_points / api_auth（订单、积分、登录三域，key 前缀隔离 + 独立密码，见 `outputs/redis_api_integration.md`）；6 个原子 Lua 脚本在服务器 `/etc/redis/scripts/`；健康检查 `/root/redis_healthcheck.sh`。FLUSHALL/FLUSHDB 已改名秘密命令，DEBUG 禁用。
- **ACL 持久化位置**：`/var/lib/redis/users.acl`（/etc/redis 只读放不了）。**ACL SAVE 后必查 default 用户密码还在**（aclfile 会覆盖 requirepass，曾导致 nopass 裸奔）。
- **应用服务器**：`39.104.28.191`（Alibaba Cloud Linux 3 / 宝塔），跑 FastAPI `auth-server`（/www/auth-server，redis-py 5.2.1）+ Java + MySQL + **本机 Redis(127.0.0.1，已有业务在用)**。Redis 节点 6379 防火墙只放行此 IP + 127.0.0.1。auth-server 的 config.py 已支持 `redis_user`（`.env` 未切，行为不变）；**RTT 39ms，热路径留本机 Redis，远程节点做 L2/跨机共享**（逐条 GET×50 实测 2473ms vs pipeline 50ms）。
- 该机器是宝塔面板环境（nginx + PHP 8.2 + MySQL），**SFTP 子系统未启用**，传文件需用 base64 + exec 通道；远程操作 SSH 用 paramiko 系统 Python（自带 paramiko 5.0.0）。应用服务器连接脚本 `tmp_redis/app_ssh.py`，Redis 节点是 `tmp_redis/sshrun.py`——**两台机器的连接脚本不要混用**。
- **异地备份**：app 03:30 推送 → RainYun `/home/bkpush/backups/`（rrsync 沙箱锁定，仅能写该目录）；RainYun 04:30 校验+清 7 天前。恢复流程见 `outputs/offsite_backup_setup.md`。注意：**app 服务器的 nginx 是系统级 /etc/nginx**（非宝塔路径）。
- **Git Bash 坑**：命令行参数中的 `/root/xxx` 会被 MSYS 转成 `C:/Program Files/Git/root/xxx` 传给 python，再拼进远程命令就会报 `C:/Program: No such file or directory`——命令前加 `MSYS_NO_PATHCONV=1`，或路径写死在脚本内。

## 技术约定
- 宝塔机器部署 Redis 的 4 个坑及修复见 `memory/2026-10-19.md`（符号链接 ExecPaths、/usr/local/lib jemalloc 白名单、unix socket、日志权限）。
- `pkill -f <name>` 会匹配 SSH 命令自身导致会话中断，改用 `pkill -x`。
- Redis 节点 6379 白名单 = 应用服务器 IP；若应用服务器 IP 变更需同步 `ufw`。

## 技术约定
- 宝塔机器部署 Redis 的 4 个坑及修复见 `memory/2026-10-19.md`（符号链接 ExecPaths、/usr/local/lib jemalloc 白名单、unix socket、日志权限）。
- `pkill -f <name>` 会匹配 SSH 命令自身导致会话中断，改用 `pkill -x`。
- 该节点当前 6379 对全网开放，待用户提供应用服务器 IP 后应改为 UFW 白名单。

## 用户偏好
- 用户以中文沟通，交付物用中文。
- 部署类任务倾向"直接做"（Agent 模式），仅网络暴露范围等安全/架构关键分叉需要确认。
