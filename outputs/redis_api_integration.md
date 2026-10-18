# Redis 接入订单 / 积分 / 账号登录 API 指南

**节点：** 122.228.216.138:6379（Redis 7.0.15）
**适用业务：** 客户端订单、积分、账号登录三大高频接口
**状态：** 已为三域分别建立独立账号 + 原子脚本，隔离与幂等均已实测

---

## 一、三个业务域账号

每个域一个独立账号，**密码不同、只能访问自己的 key 前缀**。任一业务被攻破不会波及其他域，误操作也够不到别人的数据。

| 账号 | 业务 | 可用 key 前缀 | 密码 |
|---|---|---|---|
| `api_order` | 订单 | `order:` `stock:` `idem:` `rl:order:` | `1m1YEh7YcwhK1MTAinu6E8gQnUCD` |
| `api_points` | 积分 | `points:` `rl:points:` | `cCdRTRFUgc5qEe60HMjX6AejgAGz` |
| `api_auth` | 登录 | `session:` `tok:` `login:` `rl:login:` | `pPBSXnAd9PA1VyHR0WCrbxgVyJC9` |
| `api_app` | **auth-server 单体应用**（三域合并） | `order:` `stock:` `idem:` `points:` `session:` `tok:` `login:` `rl:*` | `hKo0bU6C6GCeufmwLaUJguC8Fs8ud9bZ` |
| `default` | 运维/管理 | `*`（全量） | `M6Ig0fr4xCjGgiOGdM11u18Ppbrf5RiyeGbvrxYo` |

> `api_app` 是为单体 FastAPI 应用准备的：一个进程覆盖三个业务域，用合并账号即可，无需为每个域建连接池。三个分域账号保留，将来若拆微服务可直接复用。`api_app` 与分域账号一样被禁止 `KEYS`/`CONFIG`/`FLUSHALL`/`SHUTDOWN`，跨前缀访问实测 `NOPERM`。

> `default` 是管理员账号，**不要给业务代码用**。只允许在服务器本机或运维白名单 IP 使用。

三个业务账号统一权限：`+@all -@dangerous -@admin -@pubsub -@blocking +info`
即：读写/脚本/连接/INFO 可用；`KEYS`、`FLUSHALL`、`CONFIG`、`SHUTDOWN`、`ACL`、监控类命令一律拒绝（已实测）。

### 权限隔离实测结果

| 测试 | 结果 |
|---|---|
| api_order 读写 `order:*` | ✅ 允许 |
| api_order 访问 `points:*` / `session:*` | ❌ NOPERM |
| api_points 执行 Lua 读写 `order:*` | ❌ NOPERM（**Lua 内部也受 ACL 约束**） |
| api_auth 删除自己的 `session:*` | ✅ 允许 |
| 任一账号执行 `KEYS` / `SHUTDOWN` | ❌ NOPERM |
| 任一账号执行 `FLUSHALL` | ❌ 未知命令（已改名） |

---

## 二、Key 设计规范

统一约定：**业务数据一律带 TTL，禁止永不过期的热数据**。

| 前缀 | 含义 | 类型 | 建议 TTL | 说明 |
|---|---|---|---|---|
| `idem:order:{uid}:{reqId}` | 订单幂等标记 | String | 600–86400s | 重复请求拒绝 |
| `order:detail:{orderId}` | 订单详情缓存 | String(JSON) | 3600s | 命中则不打 MySQL |
| `order:list:{uid}:{page}` | 我的订单分页 | String(JSON) | 300s | 列表缓存 |
| `stock:{skuId}` | 商品库存（秒杀） | String(Int) | 无（预热写入） | 供原子扣减 |
| `points:bal:{uid}` | 积分余额 | String(Int) | 无 | 高速副本 |
| `points:ledger:{uid}` | 积分流水（最近1000条） | ZSET | 无 | 幂等比对 + 溯源 |
| `session:{token}` | 登录会话 | String(JSON) | 7200s | 登录态 |
| `tok:{refreshToken}` | 刷新令牌 | String | 7d | 换 access token |
| `login:fail:{account}` | 登录失败计数 | String(Int) | 900s | 爆破防护 |
| `login:lock:{account}` | 账号锁定标记 | String | 1800s | 锁定中 |
| `rl:{domain}:{ip}` | 接口限流计数 | String(Int) | 60s | 防刷 |

> 前缀规则：`业务:对象:ID`，不要用 `{uid}{id}` 这类无分隔写法，`SCAN MATCH` 会串味。

---

## 三、原子脚本（解决并发竞态）

普通 `GET` 再 `DECRBY` 两步操作在高并发下会超卖/透支。以下脚本把多步操作封进一次 Lua 调用，**Redis 单线程执行期间不会被插队**，天生原子。

脚本已部署在服务器 `/etc/redis/scripts/`，用 `EVALSHA` + SHA1 调用（避免每次传整个脚本）。

| 脚本 | 用途 | SHA1 |
|---|---|---|
| `order_dedup.lua` | 订单幂等去重 | `c6546f4a864bea41c72757a49a0e4efb81c6f677` |
| `stock_deduct.lua` | 库存原子扣减（防超卖） | `cb8074987e00e526e4ed33852466ab6c6c5f3b45` |
| `points_deduct.lua` | 积分原子扣减（防透支） | `138abc77a8384128f8295f8fa81c503f81d03444` |
| `points_credit.lua` | 积分原子入账 | `14950785aaa5f972365bf1c906db122bfb3ecd80` |
| `login_throttle.lua` | 登录失败限流 + 锁定 | `88572aacaf464046cc44340edd39f6a8e4202f02` |
| `batch_get.lua` | 批量读缓存（省往返） | `3fb3d771ee5a8e4dbfc598ce639cd4f7b4fa3d9e` |

### 返回值语义

**order_dedup** — 返回 `1` 首次（放行）／`0` 重复（拒绝）

**stock_deduct** — `>=0` 剩余库存／`-1` 库存不足／`-2` 未预热

**points_deduct** — `>=0` 扣减后余额／`-1` 余额不足／`-2` 该单号已扣过（幂等）
**points_credit** — `>=0` 入账后余额／`-2` 单号已存在（不重复发）

**login_throttle** — `0` 允许尝试／`n>0` 第 n 次失败／`-1` 已达阈值触发锁定／`-2` 已锁定直接拒

### 脚本安全性说明
- ACL 在 Lua 内部**同样生效**：api_points 即使写脚本题去动 `order:*` 也会 NOPERM，已实测。
- 不会无限增长：`points:ledger` 每写一次用 `ZREMRANGEBYRANK` 裁到最近 1000 条。
- 幂等键带 TTL，过期自动清理，无需 GC。

---

## 四、PHP 对接代码（phpredis）

宝塔 PHP 需先在面板安装 `redis` 扩展（软件商店 → PHP 8.2 → 设置 → 安装扩展 → redis）。

```php
class ApiRedis {
    private static ?\Redis $order = null;

    public static function order(): \Redis {
        if (!self::$order) {
            $r = new \Redis();
            // pconnect 长连接 + 2.5s 超时，失败快速失败不要拖垮请求
            $r->pconnect('122.228.216.138', 6379, 2.5);
            $r->auth(['user' => 'api_order', 'pass' => '1m1YEh7YcwhK1MTAinu6E8gQnUCD']);
            self::$order = $r;
        }
        return self::$order;
    }
}
```

### 1. 下单（幂等 + 防超卖 + 缓存）

```php
const SHA_DEDUP  = 'c6546f4a864bea41c72757a49a0e4efb81c6f677';
const SHA_STOCK  = 'cb8074987e00e526e4ed33852466ab6c6c5f3b45';

function placeOrder(int $uid, string $reqId, int $skuId, int $qty): array {
    $r = ApiRedis::order();

    // ① 幂等：同一 reqId 重复提交直接拒
    if (!$r->evalSha(SHA_DEDUP, ["idem:order:$uid:$reqId"], [86400])) {
        return ['code' => 409, 'msg' => '重复请求'];
    }

    // ② 库存原子扣减（关键：杜绝超卖）
    $left = $r->evalSha(SHA_STOCK, ["stock:$skuId"], [$qty]);
    if ($left === -2) return ['code' => 500, 'msg' => '库存未预热'];
    if ($left === -1) return ['code' => 410, 'msg' => '库存不足'];

    // ③ 写入 MySQL（订单表，状态=待支付）—— 这一步才是权威落库
    $orderId = createOrderInMysql($uid, $skuId, $qty);
    if (!$orderId) {
        // 落库失败要回滚 Redis 预扣，否则库存泄漏
        $r->incrBy("stock:$skuId", $qty);
        return ['code' => 500, 'msg' => '下单失败'];
    }

    // ④ 回填缓存供查询
    $r->setex("order:detail:$orderId", 3600, json_encode(['id' => $orderId]));

    return ['code' => 0, 'orderId' => $orderId, 'stockLeft' => $left];
}
```

> ⚠️ 第 ③ 步 MySQL 失败必须回滚第 ② 步的 Redis 预扣，否则库存只减不增。这是这套方案唯一必须手工处理的补偿点。

### 2. 积分消费 / 发放

```php
const SHA_PD = '138abc77a8384128f8295f8fa81c503f81d03444';
const SHA_PC = '14950785aaa5f972365bf1c906db122bfb3ecd80';

function consumePoints(int $uid, int $points, string $bizId): array {
    $p = ApiRedis::points();

    $bal = $p->evalSha(SHA_PD,
        ["points:bal:$uid", "points:ledger:$uid"],
        [$points, $bizId, intval(microtime(true) * 1000)],
        2
    );

    if ($bal === -2) return ['code' => 409, 'msg' => '该单号已扣减'];
    if ($bal === -1) return ['code' => 410, 'msg' => '积分不足'];
    return ['code' => 0, 'balance' => $bal];
}
```

### 3. 登录（防爆破 + 会话）

```php
const SHA_THROTTLE = '88572aacaf464046cc44340edd39f6a8e4202f02';

function login(string $account, string $password): array {
    $a = ApiRedis::auth();

    // ① 锁定状态检查 + 失败累加
    $t = $a->evalSha(SHA_THROTTLE,
        ["login:fail:$account", "login:lock:$account"],
        [5, 900, 1800], 2
    );
    if ($t === -1) return ['code' => 429, 'msg' => '失败过多，账号锁定30分钟'];
    if ($t === -2) return ['code' => 429, 'msg' => '账号已锁定'];

    // ② 校验密码（查 MySQL）
    $user = findUserByAccount($account);
    if (!$user || !password_verify($password, $user['pwd_hash'])) {
        return ['code' => 401, 'msg' => '账号或密码错误'];
    }

    // ③ 成功：清计数，发会话
    $a->del("login:fail:$account", "login:lock:$account");
    $token = bin2hex(random_bytes(16));
    $a->setex("session:$token", 7200, json_encode(['uid' => $user['id']]));

    return ['code' => 0, 'token' => $token];
}
```

---

## 五、数据一致性铁律（重要）

Redis 在这里是**加速层，不是权威库**。一旦搞反会出账务事故。

| 业务 | 权威库 | Redis 角色 | 补偿机制 |
|---|---|---|---|
| 订单 | MySQL | 幂等标记、库存预扣、读缓存 | 落库失败回滚 Redis；每日库存对账 |
| 积分 | **MySQL 账本** | 余额副本 + 流水记录 | 每日对账任务，漂移以 MySQL 回写 |
| 登录态 | — | 会话存储 | 丢失即重新登录，可接受 |

**积分必须遵守的先写后改顺序：**
```
1. 写 MySQL 积分流水（状态=已扣减，带唯一索引 biz_id）
2. MySQL 成功后才调 points_deduct.lua
3. MySQL 写失败则不要动 Redis
4. 每日对账：SUM(MySQL流水) vs Redis points:bal:*，不一致以 MySQL 为准
```

**订单库存同理**：秒杀场景库存以 Redis 为权威（预热保证），但正常商详库存建议以 MySQL 为准、Redis 只做并发预扣缓冲。

---

## 六、高并发调优要点

1. **用 `pconnect` 而非 `connect`**：PHP-FPM 短连接模型下每次新建 TCP 都贵，且公网 6379 握手要额外一次 AUTH 往返。
2. **批量读用 `batch_get.lua`**：列表页一次往返拿 50 条，比逐条 GET 快 50 倍往返。客户端到 Redis 公网约 85 ms，**逐条 GET 会把缓存的收益全吃掉**。
3. **写操作走 pipeline/MULTI**：批量缓存回填合并往返。
4. **进程内加二级缓存**（APCu / 静态变量）：极热数据（配置、分类）本地命中，完全不打 Redis。
5. **连接数控制**：PHP-FPM 32 workers × 长连接 ≤ `maxclients 10000`，但注意 MySQL 也在吃内存，建议 PHP 侧连接池 50 以内。
6. **缓慢失败**：`pconnect` 超时 2.5s，Redis 抖动时不要让 PHP 全部阻塞等它。
7. **不要用 `KEYS`**：已禁用。遍历一律 `SCAN MATCH pattern COUNT 100`。

---

## 七、日常运维

### 清空缓存（FLUSHALL 已改名，防误触发）
```bash
# 只有管理员账号能执行，业务账号调用会 NOPERM
redis-cli -h 122.228.216.138 -a '<admin密码>' wbCacheFlushAll_9Kd2Qm
# 直接写 FLUSHALL 会得到 unknown command
```
- `FLUSHALL` → 秘密名 `wbCacheFlushAll_9Kd2Qm`
- `FLUSHDB` → 秘密名 `wbCacheFlushDb_4Vn7Ts`
- `DEBUG` 已彻底禁用

### 健康检查
```bash
bash /root/redis_healthcheck.sh     # 退出码 0=健康 1=异常
```
检查服务状态、内存水位、缓存命中率（低于 70% 会告警）、连接数、淘汰量、慢查询、ACL 账号完整性、无密码访问风险。已部署到服务器。

### ACL 变更流程（有坑，慎入）
```bash
redis-cli -a '<admin密码>' ACL SETUSER api_order on '>新密码' ~order:* +@all -@dangerous -@admin
redis-cli -a '<admin密码>' ACL SAVE          # 持久化，必须执行
redis-cli -a '<admin密码>' ACL LIST          # 复查 default 用户是否还有密码
```
> ⚠️ `aclfile` 写在 `/var/lib/redis/users.acl`。部署时曾出现 `ACL SAVE` 后 default 用户变 `nopass` 的情况（aclfile 覆盖了 `requirepass`）。**每次 `ACL SAVE` 后务必复查 default 用户仍有密码**，否则公网端口裸奔。

### 常用排查
```bash
redis-cli -a '<密码>' INFO stats | grep keyspace      # 命中/未命中
redis-cli -a '<密码>' --stat                            # 实时流量
redis-cli -a '<密码>' SLOWLOG GET 20                   # 慢查询
redis-cli --user api_order -a '<订单密码>' SCAN 0 MATCH 'order:*' COUNT 100
```

---

## 八、应用服务器接入实录（39.104.28.191，2026-10-19 完成）

应用服务器实况：Alibaba Cloud Linux 3 / 宝塔面板，nginx(80/443) + FastAPI(`auth-server.service`，uvicorn 127.0.0.1:8000×2 workers) + Java(3000/8080) + MySQL + **本机 Redis(127.0.0.1:6379，已有 8 客户端在用)**。

### 已完成
1. **防火墙已收紧**：6379 从"全网开放"改为只放行 `39.104.28.191` 与 `127.0.0.1`。已从第三方 IP 实测连接超时被拒；应用服务器实测 api_order / api_points / api_auth / api_app 全部 `PONG`，跨域写、Lua 内跨域读均 `NOPERM`。
2. **应用配置已支持 ACL 用户名**（`/www/auth-server/app/config.py`，备份 `config.py.bak.redisuser`；本地仓库同步修改）：
   新增 `redis_user` 字段、`redis_url` 支持 `redis://user:pass@host/db` 三段式。**`.env` 未改、服务未重启，当前行为与改动前完全一致**（`redis://127.0.0.1:6379/0`，已实测验证 + py_compile 通过）。
3. **端到端实测**（应用 venv redis-py 5.2.1 → 远程节点）：PING / SETEX / GET / EVAL 全通，版本确认 7.0.15。

### 实测延迟数据（决策依据，务必看）

| 操作 | 耗时 |
|---|---|
| 应用服务器 → 远程节点 RTT | **39 ms/往返** |
| 本机 Redis（127.0.0.1）单次操作 | **<1 ms** |
| pipeline 批量 50 SET（1 次往返） | 50 ms |
| MGET 50 keys（1 次往返） | 50 ms |
| **循环逐条 GET ×50（50 次往返）** | **2473 ms** |

**结论：不要把 auth-server 的 Redis 整体切到远程节点。** 每次 API 请求有多次 Redis 往返（会话校验、登录 state、限流计数），整体切换会让单请求多出 ~100–200ms，是性能倒退。本机 Redis 仍是热路径首选。

### 推荐架构：本机 L1 + 远程 L2
- **本机 Redis（127.0.0.1）**：会话、登录限流、登录 state、pub/sub——每请求必经的热路径
- **远程节点（122.228.216.138）**：订单幂等键、积分余额原子操作、订单/积分批量读，以及**跨机共享状态**——将来加第二台应用服务器时，两台必须共享会话/幂等/积分，这才是它的核心价值

### 如仍要切换（步骤已就绪，自行决定）
```bash
# 在 /www/auth-server/.env 追加：
REDIS_HOST=122.228.216.138
REDIS_USER=api_app
REDIS_PASSWORD=hKo0bU6C6GCeufmwLaUJguC8Fs8ud9bZ
# 重启生效（回滚：删掉这三行再重启，改动是纯增量的）：
systemctl restart auth-server
```
注意：远程节点 `maxmemory 384mb` / `maxclients 10000`；`ws_manager` 的 pub/sub 建议仍走本机以降低延迟。

---

## 九、遗留事项

1. **积分对账任务**：需在业务服务器建定时任务比对 MySQL 与 Redis 余额，可另行提供脚本。
2. **消息队列**：远程节点 `appendonly no`（纯缓存定位），若要存不能丢的队列数据需先改 AOF。
3. **本机 Redis 无密码**：app 服务器上的 127.0.0.1 Redis 未设密码，仅监听回环所以风险可控，建议加 `requirepass`。
4. 应用服务器磁盘已用 73%（27G/40G），暂无需处理，留意增长。
