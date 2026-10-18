# Redis 缓存节点部署报告

**服务器：** 122.228.216.138（Debian 12 bookworm，2 vCPU / 1.9 GB RAM）
**部署时间：** 2026-10-19
**版本：** Redis 7.0.15（Debian bookworm 官方源，安全维护版）
**状态：** ✅ 运行中，已开机自启

---

## 一、连接信息

| 项目 | 值 |
|---|---|
| 地址 | `122.228.216.138` |
| 端口 | `6379` |
| 密码 | `M6Ig0fr4xCjGgiOGdM11u18Ppbrf5RiyeGbvrxYo` |

```bash
# 连接
redis-cli -h 122.228.216.138 -p 6379 -a 'M6Ig0fr4xCjGgiOGdM11u18Ppbrf5RiyeGbvrxYo'
```

> 生产环境请改用连接池，不要把密码硬编码在代码里。

---

## 二、完成的配置

### 基础
- `redis-server` / `redis-tools` 7.0.15 已安装，`systemctl` 托管，`enabled` 开机自启
- 重启存活已实测验证（`systemctl restart` 后立即恢复）

### 内存策略（针对 1.9 GB 小内存机器）
| 参数 | 值 | 说明 |
|---|---|---|
| `maxmemory` | 384 MB | 硬上限，本机还要跑 MySQL/PHP/宝塔面板，不能吃满 |
| `maxmemory-policy` | `allkeys-lru` | 淘汰最近最少使用的 key，适合纯缓存 |
| `maxclients` | 10000 | |
| `LimitNOFILE` | 65535 | systemd 覆盖设置 |

> 384 MB 是保守值。上线后观察 `INFO memory`，若内存有余量可逐步调到 512 MB。

### I/O 与延迟
- `tcp-backlog 8192`，`net.core.somaxconn=8192`，`tcp_max_syn_backlog=8192`（应对高并发连接突发）
- `tcp-keepalive 300`，`timeout 0`
- `lazyfree-lazy-*` 全部开启（淘汰/过期时不阻塞主线程，避免大 key 删除造成卡顿）
- 2 核机器未开 `io-threads`（该特性在 4 核以上才有收益）
- **THP 已关闭**（`/sys/kernel/mm/transparent_hugepage/enabled = never`）。THP 是 Redis 延迟毛刺的常见元凶
- `vm.overcommit_memory=1`（否则后台 RDB fork 可能失败）
- 新增 2 GB swap（原系统无 swap，fork 时有 OOM 风险）

### 持久化
| 参数 | 值 |
|---|---|
| `save` | `900 1` / `300 10` |
| `appendonly` | `no` |

按**纯缓存**定位：只做轻量 RDB 快照，不开 AOF，换取最高写入吞吐。重启后热数据可从 RDB 恢复。

> ⚠️ 如果后续要用 Redis 存**消息队列、订单等不能丢的数据**，必须改 `appendonly yes`，并告知我。

### 安全
- `requirepass` 已设置（40 位随机）
- `protected-mode yes`
- `bind 0.0.0.0`（按你的选择开放公网，无 AUTH 连接一律拒绝，已实测）
- `FLUSHALL` / `FLUSHDB` / `DEBUG` 已禁用（防止误操作全库清空）
- 防火墙已放行 `6379/tcp`（v4 + v6）

---

## 三、性能实测

本地回环（服务器自身 2 核）：

| 场景 | QPS |
|---|---|
| SET，50 并发 + pipeline(8) | ~469,000 |
| GET，50 并发 + pipeline(8) | ~462,000 |
| SET，200 并发 | ~59,000 |
| GET，200 并发 | ~78,000 |
| GET，500 并发 | ~79,000 |
| LPUSH/LPOP，100 并发 | ~70,000–82,000 |

延迟：`--latency` 采样平均 **0.12 ms**，最大 100 µs。
持久层（RDB fork）进程间通信与 lazyfree 均正常。

公网端到端（从外部经防火墙 + 密码认证）已通过完整测试：AUTH、SET/GET/TTL、INCR 计数、禁用命令均符合预期。

---

## 四、接入官网 / 客户端 API

### PHP（宝塔 PHP 8.2）
1. 宝塔面板 → 软件商店 → PHP 8.2 → 设置 → 安装扩展 → **redis**
2. 代码中：
```php
$r = new Redis();
$r->connect('122.228.216.138', 6379);
$r->auth('M6Ig0fr4xCjGgiOGdM11u18Ppbrf5RiyeGbvrxYo');
// 带连接池（多 workers 时注意连接数上限）
$r->pconnect(...);
```

**提高命中率的三个用法：**
- **页面/接口缓存**：`SET api:user:{id} $json EX 300`，读不到再查 MySQL
- **批量读取**：用 `MGET`/pipeline，别在循环里逐条 `GET`
- **限流**：`INCR rate:{ip}:{minute}` + `EXPIRE` 60，避免每次打 MySQL

### key 命名建议
```
site:page:{url}           官网页面缓存
api:user:{id}             用户信息
api:query:{md5}           查询结果缓存
rate:{ip}:{minute}        接口限流计数
session:{sid}             如需集中会话
```

### 客户端（App/小程序）
同一套地址密码，走 TCP 长连接池，避免每次请求新建连接。

---

## 五、两个需要你注意的问题

### 1. 网络延迟（重要）
从测试机到本节点的公网 RTT 实测约 **85 ms**（服务器在海外）。这个延迟与 Redis 无关，是物理链路。

- 如果官网/API 服务器也在海外同区域 → 没问题
- 如果部署在国内（如阿里云/腾讯云）→ 每次缓存往返会多出 ~40 ms，**可能让接口比直连 MySQL 更慢**
- 建议：把应用服务器放到与 Redis 同区域；或把 Redis 迁到应用同机房

缓解做法：多用 pipeline / MGET 批量操作，减少往返次数。

### 2. 6379 当前对全网开放
一旦知道官网/API 服务器的公网 IP，建议收紧：
```bash
ufw delete allow 6379/tcp
ufw allow from <你的服务器IP> to any port 6379 proto tcp
ufw reload
```
Redis 端口是被全网扫描爆破的重灾区，40 位密码目前足够，但白名单才是根治。

---

## 六、日常运维命令

```bash
# 服务状态 / 重启
systemctl status redis-server
systemctl restart redis-server

# 连接
redis-cli -h 127.0.0.1 -p 6379 -a '<密码>' --no-auth-warning

# 内存命中率
redis-cli -a '<密码>' info stats | grep keyspace
redis-cli -a '<密码>' info memory

# 慢查询
redis-cli -a '<密码>' slowlog get 20

# 实时流量
redis-cli -a '<密码>' --stat
```

**回滚备份：** 原配置在 `/etc/redis/redis.conf.bak.20261019-003838`

---

## 七、排障记录（本次部署遇到的环境问题）

部署过程中解决了 4 个环境缺陷，均已修复并固化到磁盘配置：

| # | 问题 | 原因 | 修复 |
|---|---|---|---|
| 1 | 服务无法启动，exit 127 | Debian 包把 `/usr/bin/redis-server` 做成指回 `redis-check-rdb` 的符号链接，而 unit 里 `NoExecPaths=/` 白名单只认真实路径 | 把符号链接替换为真实二进制文件 |
| 2 | `failed to map segment from shared object` | `libjemalloc.so.2` 在 `/usr/local/lib`（宝塔面板自建 jemalloc，经 `ld.so.conf` 优先），不在 `ExecPaths` 白名单内，`mmap(PROT_EXEC)` 被拒 | systemd drop-in 追加 `ExecPaths=/usr/local/lib` |
| 3 | `Failed opening Unix socket` | 配置了本地 unix socket，但 `RuntimeDirectory` 目录未生成 | 移除 unixsocket，客户端均走 TCP（更通用） |
| 4 | 启动后无任何日志即退出 | 日志文件由 root 的测试进程创建，`redis` 用户不可写 | `chown redis:redis /var/log/redis` |

配置文件：`/etc/redis/redis.conf`
systemd 覆盖：`/etc/systemd/system/redis-server.service.d/override.conf`
内核参数：`/etc/sysctl.d/99-redis.conf`
THP 服务：`/etc/systemd/system/disable-thp.service`
