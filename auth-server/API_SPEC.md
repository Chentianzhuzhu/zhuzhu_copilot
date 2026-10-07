# zhuzhu Copilot 账号系统 API 规范

> 本文档是后端、Web前端、客户端三端的共同契约。所有实现必须严格遵循。

## 基础信息

- 后端地址：`http://<host>:8000`
- WebSocket 地址：`ws://<host>:8000/ws`
- 静态文件：`/static/`
- 登录页：`/static/login/index.html`
- 管理后台：`/static/admin/index.html`
- 所有 API 返回 JSON，字符集 UTF-8
- 认证方式：Bearer Token（Header: `Authorization: Bearer <token>`）

## 数据库表结构

### users 用户表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT AUTO_INCREMENT PK | 用户ID |
| username | VARCHAR(32) UNIQUE NOT NULL | 用户名 |
| password_hash | VARCHAR(255) NOT NULL | bcrypt 密码哈希 |
| security_question | VARCHAR(128) NOT NULL | 密保问题 |
| security_answer | VARCHAR(255) NOT NULL | 密保答案（哈希存储） |
| avatar | VARCHAR(512) DEFAULT '' | 头像URL |
| points | INT DEFAULT 500 | 当前积分 |
| membership_type | ENUM('free','pro','max') DEFAULT 'free' | 会员类型 |
| membership_expire | DATETIME NULL | 会员到期时间 |
| status | ENUM('active','disabled','deleted') DEFAULT 'active' | 账号状态 |
| register_ip | VARCHAR(45) | 注册IP |
| created_at | DATETIME DEFAULT CURRENT_TIMESTAMP | 注册时间 |
| updated_at | DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE | 更新时间 |

### points_log 积分流水表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT AUTO_INCREMENT PK | |
| user_id | INT NOT NULL | 用户ID |
| change_amount | INT NOT NULL | 变动量（正=增加，负=消耗） |
| balance_after | INT NOT NULL | 变动后余额 |
| reason | VARCHAR(64) | 原因：consume/register/membership/purchase/admin_adjust |
| detail | VARCHAR(256) | 详情 |
| created_at | DATETIME DEFAULT CURRENT_TIMESTAMP | |

### orders 订单表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(32) PK | 订单号 |
| user_id | INT NOT NULL | 用户ID |
| order_type | ENUM('membership','points') | 订单类型 |
| product_id | VARCHAR(32) | 商品ID：pro_monthly/max_monthly/points_150/points_750/points_1500 |
| amount | DECIMAL(10,2) | 金额（元） |
| points_amount | INT | 积分数量（会员为每月赠送积分） |
| pay_method | ENUM('alipay','wechat') | 支付方式 |
| status | ENUM('pending','paid','cancelled','refunded') DEFAULT 'pending' | |
| created_at | DATETIME | |
| paid_at | DATETIME NULL | |

### ip_registry IP注册表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT AUTO_INCREMENT PK | |
| ip | VARCHAR(45) UNIQUE NOT NULL | IP地址 |
| user_id | INT NOT NULL | 关联用户ID |
| created_at | DATETIME | |

### admins 管理员表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INT AUTO_INCREMENT PK | |
| username | VARCHAR(32) UNIQUE NOT NULL | |
| password_hash | VARCHAR(255) NOT NULL | |
| created_at | DATETIME | |

### system_config 系统配置表
| 字段 | 类型 | 说明 |
|------|------|------|
| config_key | VARCHAR(64) PK | |
| config_value | TEXT | |
| updated_at | DATETIME | |

预置配置：
- `alipay_qrcode` = 支付宝收款码图片路径
- `wechat_qrcode` = 微信收款码图片路径

## Redis Key 设计

| Key | 类型 | TTL | 说明 |
|-----|------|-----|------|
| `login_fail:{ip}` | STRING (counter) | 300s | 登录失败次数（5分钟窗口） |
| `login_lock:{ip}` | STRING | 300s | IP登录锁定标记 |
| `register_ip:{ip}` | STRING | 永久(MySQL持久化) | IP注册标记 |
| `session:{token}` | HASH | 86400s | 会话：user_id, username, avatar |
| `points_cache:{user_id}` | STRING | 300s | 积分缓存 |
| `ws_user:{user_id}` | SET | - | 用户WebSocket连接ID集合 |
| `admin_session:{token}` | HASH | 7200s | 管理员会话 |
| `login_state:{state}` | STRING(json) | 600s | 登录握手 state（防 CSRF/重放；登录/注册阶段非破坏性校验） |
| `login_result:{state}` | STRING(json) | 600s | 登录结果中转（服务端中转回跳；`GETDEL` 单次取走，防重放） |
| `auth_session:{jti}` | STRING(json) | 86400s | 服务端会话（滑动续期，登出/强制下线即撤销） |
| `user_sessions:{user_id}` | SET | 86400s | 该用户全部会话 jti（强制下线批量撤销） |

## HTTP API

### 0. 申请登录 state（防 login CSRF / 重放）
`POST /api/auth/state?platform=<pid>&version=<ver>`

客户端在打开登录页**之前**调用，获得一次性随机 state（64 位 hex，Redis 存储 TTL 600s）。
该 state 需拼进登录页 URL，并在后续登录/注册请求体中回传，服务端校验。

> 说明：登录/注册阶段对 state 做**非破坏性校验**（`verify_state`），因为该 state 还要
> 用于写入「登录结果中转」；真正的一次性/防重放由 `login_result` 的 `GETDEL` 与 TTL 保证。

成功响应 (200)：
```json
{
  "code": 0,
  "message": "ok",
  "data": { "state": "64位hex", "expires_in": 600 }
}
```

> 服务端 `REQUIRE_LOGIN_STATE=true` 时，未携带有效 state 的登录/注册一律 400。

### 0b. 登录结果中转回跳（解决浏览器 localhost 回调被拦截）

桌面应用除监听 `http://127.0.0.1:<port>/callback` 外，还会**轮询**本接口取回登录结果。
登录页在拿到 token 后会把结果 POST 到服务端，桌面客户端再取走，从而绕开
「HTTPS 登录页 → http://127.0.0.1 子资源请求」被浏览器混合内容/私有网络策略拦截的问题。

`POST /api/auth/login_result`（登录页 → 服务端）
```json
{ "state": "64位hex", "token": "jwt", "user": { "...": "..." } }
```
- 仅在 state 仍有效时接受，否则 `400 登录校验已失效`。

`GET /api/auth/login_result?state=<state>`（桌面客户端轮询）
```json
{ "code": 0, "data": { "token": "jwt", "user": {...} } }   // 就绪
{ "code": 0, "data": null }                                 // 尚未就绪，继续轮询
```
- 一次性：结果取走即删（`GETDEL`），再次 GET 返回 `data: null`。

### 1. 注册
`POST /api/auth/register`

请求体：
```json
{
  "username": "string (3-32字符)",
  "password": "string (6-64字符)",
  "password_confirm": "string",
  "security_question": "string",
  "security_answer": "string",
  "avatar": "string (base64编码图片, 可选，默认使用系统头像)",
  "state": "string (登录握手 state，必填)"
}
```

成功响应 (200)：
```json
{
  "code": 0,
  "message": "注册成功",
  "data": {
    "user_id": 1,
    "username": "test",
    "token": "jwt_token_string"
  }
}
```

错误响应：
- `400` 参数错误 / 密码不一致 / **登录校验已失效（state 无效或已被消费）**
- `409` 用户名已存在 / 该IP已注册过账号
- `429` 注册过于频繁

### 2. 登录
`POST /api/auth/login`

请求体：
```json
{
  "username": "string",
  "password": "string",
  "state": "string (登录握手 state，必填)"
}
```

> `state` 无效或已被使用返回 `400 登录校验已失效，请返回应用重新登录`；
> 凭证错误返回 `401`（统一提示，含 `X-Attempts-Left` 剩余尝试次数）。

请求体：
```json
{
  "username": "string",
  "password": "string"
}
```

成功响应：
```json
{
  "code": 0,
  "message": "登录成功",
  "data": {
    "token": "jwt_token",
    "user": {
      "id": 1,
      "username": "test",
      "avatar": "/static/uploads/avatars/xxx.png",
      "points": 500,
      "membership_type": "free",
      "membership_expire": null
    }
  }
}
```

错误：
- `401` 用户名或密码错误
- `403` 账号已被禁用
- `429` 5分钟内失败超过5次，IP锁定

### 3. 获取用户信息
`GET /api/auth/me`
Header: `Authorization: Bearer <token>`

响应：同登录的 user 对象。

### 4. 登出
`POST /api/auth/logout`
Header: `Authorization: Bearer <token>`

### 5. 上传头像（注册时可用base64，登录后可单独上传）
`POST /api/auth/avatar`
Header: `Authorization: Bearer <token>`
Content-Type: `multipart/form-data`
字段：`file` (图片文件，最大2MB，支持jpg/png/gif)

响应：`{"code":0, "data":{"avatar":"/static/uploads/avatars/xxx.png"}}`

### 6. 积分查询
`GET /api/points/balance`
Header: Authorization

响应：`{"code":0, "data":{"points":500, "membership_type":"free"}}`

### 7. 积分消耗（客户端调用，内置模型使用时）
`POST /api/points/consume`
Header: Authorization

请求体：
```json
{
  "amount": 1,
  "model": "agens",
  "task_id": "optional"
}
```

响应：
```json
{
  "code": 0,
  "data": {"remaining": 499}
}
```
错误：`402` 积分不足 `{"code":402, "message":"积分不足", "data":{"remaining":0}}`

### 8. 商品列表
`GET /api/shop/products`

响应：
```json
{
  "code": 0,
  "data": {
    "memberships": [
      {"id":"pro_monthly", "name":"Pro版", "price":7, "points":1000, "duration_days":30},
      {"id":"max_monthly", "name":"Max版", "price":14, "points":2000, "duration_days":30}
    ],
    "point_packs": [
      {"id":"points_150", "name":"150积分", "price":1, "points":150},
      {"id":"points_750", "name":"750积分", "price":5, "points":750},
      {"id":"points_1500", "name":"1500积分", "price":10, "points":1500}
    ]
  }
}
```

### 9. 创建订单
`POST /api/shop/order`
Header: Authorization

请求体：
```json
{
  "product_id": "pro_monthly",
  "pay_method": "alipay"
}
```

响应：
```json
{
  "code": 0,
  "data": {
    "order_id": "ORD202610060001",
    "product_id": "pro_monthly",
    "amount": 7,
    "pay_method": "alipay",
    "qrcode_url": "/static/uploads/qrcodes/alipay.png",
    "status": "pending"
  }
}
```

### 10. 查询订单状态
`GET /api/shop/order/{order_id}`
Header: Authorization

### 11. 获取收款码
`GET /api/shop/qrcodes`

响应：
```json
{
  "code": 0,
  "data": {
    "alipay": "/static/uploads/qrcodes/alipay.png",
    "wechat": "/static/uploads/qrcodes/wechat.png"
  }
}
```

### 12. 用户协议
`GET /api/agreement`
返回纯文本或HTML的用户协议内容。

---

### 管理员 API（需 Admin Token）

### A1. 管理员登录
`POST /api/admin/login`
请求体：`{"username":"admin","password":"admin123"}`
响应：`{"code":0,"data":{"token":"admin_token"}}`

### A2. 用户列表
`GET /api/admin/users?page=1&page_size=20&keyword=xxx`
Header: `Authorization: Bearer <admin_token>`

响应：
```json
{
  "code": 0,
  "data": {
    "total": 100,
    "page": 1,
    "list": [
      {"id":1,"username":"test","avatar":"...","points":500,"membership_type":"free","status":"active","register_ip":"1.2.3.4","created_at":"2026-10-01 12:00:00"}
    ]
  }
}
```

### A3. 禁用用户
`POST /api/admin/users/{user_id}/disable`
Header: Admin Authorization

行为（幂等，重复禁用不报错）：
1. 置 `status=disabled`；
2. 经 WebSocket 推送 `force_logout`（连接仍在时立即送达）；
3. 撤销该用户全部服务端会话 → 后续 API 一律 401/403、WS 不再放行。

客户端收到 `force_logout` 后：清除本地凭证、中断正在运行的内置模型任务并弹窗提示。

### A4. 解封用户
`POST /api/admin/users/{user_id}/enable`
置 `status=active`；解封后用户可**立即**重新登录使用（幂等，未禁用时也返回成功）。

### A5. 删除用户
`DELETE /api/admin/users/{user_id}`

软删除（`status=deleted`），并依次：推送 `force_logout` → 撤销全部会话。
（此前该接口因缺少 Redis 依赖会 500，已修复。）

> **强制下线兜底**：若 WS 恰好断线未收到推送，客户端在积分上报收到 `403` 时会立即强制下线；
> 周期巡检（`GET /api/auth/me`）也能在 403 时触发强制下线；登录接口对已禁用账号返回 `403 账号已被禁用`。

### A6. 调整积分
`POST /api/admin/users/{user_id}/points`
请求体：`{"amount":100, "reason":"admin_adjust", "detail":"手动充值"}`
amount 正为增加，负为扣减。响应后通过 WebSocket 推送 `points_update`。

### A7. 设置收款码
`POST /api/admin/qrcode`
Header: Admin Authorization
Content-Type: multipart/form-data
字段：`type` (alipay/wechat), `file` (图片)

返回 `{"code":0,"data":{"qrcode_url":"/static/uploads/qrcodes/<type>_<hex>.png"}}`。

> **路径前缀注意**：返回的是**根相对路径**（`/static/...`），而服务实际挂载在
> `/authapp/` 之下。管理后台与桌面客户端读取后都必须补上部署前缀
> （`/authapp/static/...`）才能正确显示，否则会打到站点根（其他服务）而 404。

### A8. 确认订单到账（手动核对）
`POST /api/admin/orders/{order_id}/confirm`
将订单标记为 paid，发放积分/会员。

### A9. 订单列表
`GET /api/admin/orders?page=1&page_size=20&status=pending`

## WebSocket 协议

连接：`ws://<host>:8000/ws?token=<user_token>`

### 客户端 → 服务端消息
```json
{"type":"ping"}
```

### 服务端 → 客户端消息

**1. 积分变动推送**
```json
{
  "type": "points_update",
  "data": {
    "points": 450,
    "change": -50,
    "reason": "consume"
  }
}
```

**2. 强制下线推送**
```json
{
  "type": "force_logout",
  "data": {
    "reason": "账号已被管理员禁用"
  }
}
```

**3. 账号状态变更**
```json
{
  "type": "account_status",
  "data": {
    "status": "disabled"
  }
}
```

**4. 订单支付成功通知**
```json
{
  "type": "order_paid",
  "data": {
    "order_id": "ORDxxx",
    "points_added": 1000
  }
}
```

**5. 心跳 pong**
```json
{"type":"pong"}
```

### 心跳机制
- 客户端每 30 秒发送 `{"type":"ping"}`
- 服务端回复 `{"type":"pong"}`
- 超过 90 秒未收到 ping 则断开连接
- 客户端断线后自动重连（指数退避，最大间隔 30 秒）

## 积分消耗规则

- 默认 agens 模型消耗系数：0.03x
- 消耗计算：每生成 1000 tokens 消耗 `1000 * 0.03 = 30` 积分
- 客户端按 token 增量实时调用 `/api/points/consume`
- 积分归零时：服务端返回 402，客户端立即截断任务，在输出末尾追加固定文案：
  `您的积分不足，请接入其他AI服务`
- Pro/Max 会员不改变消耗速率，仅提供每月积分额度

## 安全限制

1. **IP注册限制**：每个IP只能注册一个账号（检查 ip_registry 表 + Redis 缓存）
2. **登录失败限流**：同一IP 5分钟内最多5次失败，超限锁定5分钟
3. **密码加密**：bcrypt（cost=12）
4. **密保答案**：bcrypt 哈希存储
5. **头像校验**：最大 2MB，仅支持 jpg/jpeg/png/gif，用 Pillow 验证文件头
6. **Token**：JWT（HS256），有效期 24 小时
