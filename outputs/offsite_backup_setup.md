# 异地备份仓库部署报告

**备份链路：** 应用服务器 39.104.28.191（阿里云）→ RainYun 节点 122.228.216.138
**上线时间：** 2026-10-19
**状态：** ✅ 首次全量备份已完成并校验，双侧定时任务已启用

---

## 一、架构与安全模型

**推送模式**：应用服务器每天凌晨把备份推到 RainYun 节点。

```
app server 03:30                    RainYun 04:30
mysqldump ─┐
tar 代码+配置 ─┼→ rsync over SSH ──→ /home/bkpush/backups/<日期>/
校验和 ─┘    (专用密钥)              └─ root 定时: 校验+清理7天前+过期告警
```

**防"备份被一起删掉"设计**（关键）：
- 应用服务器上的推送密钥是 **ed25519 专用密钥**，在 RainYun 侧被 `command="/usr/bin/rrsync /home/bkpush/backups",restrict` 锁死
- 该密钥**只能**向 `/home/bkpush/backups/` 内写文件：不能执行命令、不能读系统、不能越出该目录（`:/tmp/` 会被压回沙箱内，`../../` 穿越、执行 `ls /` 均实测被拒）
- 7 天保留期的**删除动作只在 RainYun 由 root 定时执行**——应用服务器被攻破也删不掉历史备份
- 备份目录属主为专用低权限用户 `bkpush`，无 sudo

## 二、备份内容

| 文件 | 内容 | 首次实测大小 |
|---|---|---|
| `mysql_<日期>.sql.gz` | 全部 5 个业务库（mysql/doudizhu/lost_found/zhuzhu_copilot_auth/winapp_update），`--single-transaction` InnoDB 一致性快照，含建库语句/存储过程/触发器 | 1.4M |
| `files_<日期>.tar.gz` | `/www/auth-server`（含 `.env`，排除 .venv/__pycache__）+ `/etc/nginx` 全部配置 | 12M |
| `manifest.txt` | 主机名/库清单/nginx 版本/内核/crontab 快照 | - |
| `SHA256SUMS` | 三个文件的哈希，推送前本地校验一次、RainYun 每日复验一次 | - |

单日总量约 14M，7 天保留约 100M，RainYun 磁盘（23G 空闲）无压力。

## 三、定时任务

| 机器 | 时间(服务器时区) | 任务 | 日志 |
|---|---|---|---|
| 应用服务器 | 03:30 | `/root/offsite_backup.sh` | `/var/log/offsite_backup.log` |
| RainYun | 04:30 | `/usr/local/sbin/offsite_retention.sh` | `/var/log/offsite_retention.log` |

**retention 脚本每次做四件事**：校验最新备份集哈希 → 删除 7 天前的备份集 → 检查最新备份是否超过 30 小时（陈旧告警写日志）→ 输出剩余磁盘和保留份数。

## 四、恢复流程（灾难演练时照这个来）

```bash
# 0) 在 RainYun 上取回备份（root）
scp -r root@122.228.216.138:/home/bkpush/backups/<日期> /root/restore/

# 1) 校验完整性
cd /root/restore/<日期> && sha256sum -c SHA256SUMS

# 2) 恢复数据库（dump 含 CREATE DATABASE，直接导入即可）
zcat mysql_<日期>.sql.gz | mysql -u root -p

# 3) 恢复文件（tar 内是绝对路径 www/... etc/...）
tar xzf files_<日期>.tar.gz -C /
# 注意：.env 在 www/auth-server/.env 里，恢复后核对 JWT_SECRET/MYSQL_PASSWORD

# 4) 重启服务
systemctl restart auth-server nginx mysqld
```

## 五、运维命令

```bash
# 手动触发一次备份（应用服务器）
bash /root/offsite_backup.sh
tail -20 /var/log/offsite_backup.log

# 手动跑一次保留策略/完整性校验（RainYun）
bash /usr/local/sbin/offsite_retention.sh
cat /var/log/offsite_retention.log

# 看远端备份列表（RainYun）
ls -lt /home/bkpush/backups/

# 备份大小监控（RainYun）
du -sh /home/bkpush/backups/* | sort -k2
```

## 六、安全负面测试记录（2026-10-19 实测）

| 攻击向量 | 结果 |
|---|---|
| 用备份密钥执行 `ls /` | ❌ `SSH_ORIGINAL_COMMAND does not run rsync` |
| 用备份密钥 rsync 到 `:/tmp/` | ✅ 落在沙箱内 `backups/tmp/`，系统 /tmp 无文件（已实测确认并清理） |
| 路径穿越 `:../../tmp/` | ❌ rsync 协议错误拒绝 |
| 正常写入 `backups/<日期>/` | ✅ 成功 |

## 七、已知限制与后续可选项

1. **静态未加密**：备份文件在 RainYun 上未做静态加密（含 `.env` 与数据库明文）。RainYun root 账号本身是最大信任边界；如需进一步加固，可加 `openssl enc` 对称加密（会失去直接 grep 抽查能力），或改用 restic+bchunk 加密仓库。
2. **无告警通道**：备份失败目前只写日志，不会主动通知。RainYun 侧 `offsite_retention.log` 里出现 `stale: WARN` / `integrity: FAILED` 即异常；如需推送（Server酱/邮件/企微机器人）可再加心跳检查。
3. **保留 7 天**：如需更长（如每月留一份长期归档），改 `/usr/local/sbin/offsite_retention.sh` 的 `KEEP_DAYS` 并加月度归档逻辑。
4. RainYun 的 Redis 备份（dump.rdb）未包含在本链路中——缓存数据可重建，无备份价值；若以后存了不可丢数据再纳入。

## 八、本次部署变更清单

| 机器 | 变更 |
|---|---|
| 应用服务器 | 新增 `/root/.ssh/bkpush_ed25519`（专用密钥）、`/root/.my_bak.cnf`(600)、`/root/offsite_backup.sh`、crontab 03:30、安装 rsync |
| RainYun | 新增用户 `bkpush`（rrsync 限制）、`/home/bkpush/backups/`、`/usr/local/sbin/offsite_retention.sh`、crontab 04:30、安装 rsync |
| 双方 | `known_hosts` 已互 pin（应用侧固定 RainYun 指纹，防中间人） |
