# zhuzhu Copilot 账号系统 — 完整部署与使用指南

## 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    PyQt6 桌面客户端                        │
│  ┌──────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │ 登录按钮  │→│ 浏览器登录页   │→│ 本地回调:18765    │  │
│  └──────────┘  └──────────────┘  └────────┬─────────┘  │
│  ┌──────────┐  ┌──────────────┐           │            │
│  │ 内置模型  │→│ 积分实时扣减   │◄──────────┘            │
│  └──────────┘  └──────────────┘                        │
│  ┌──────────────────────────────────────────────┐      │
│  │ WebSocket 长连接（积分推送/强制下线/订单到账）  │      │
│  └───────────────────────┬──────────────────────┘      │
└──────────────────────────┼──────────────────────────────┘
                           │ ws://host:8000/ws
                           │ http://host:8000/api
┌──────────────────────────▼──────────────────────────────┐
│              FastAPI 后端服务 (:8000)                     │
│  ┌─────────┐ ┌──────────┐ ┌────────┐ ┌──────────────┐  │
│  │ 认证API  │ │ 积分API   │ │ 商城API │ │ 管理员API     │  │
│  └─────────┘ └──────────┘ └────────┘ └──────────────┘  │
│  ┌──────────────────────────────────────────────────┐  │
│  │ WebSocket 管理器（user_id → connections）          │  │
│  └──────────────────────────────────────────────────┘  │
└──────────┬───────────────────────┬──────────────────────┘
           │                       │
    ┌──────▼──────┐        ┌───────▼───────┐
    │   MySQL     │        │    Redis      │
    │ 用户/积分/  │        │ 登录限流/会话/ │
    │ 订单/管理员 │        │ 积分缓存/推送  │
    └─────────────┘        └───────────────┘
```

## 一、环境准备（Windows）

### 1.1 安装 MySQL 8.0

1. 下载 MySQL Installer：https://dev.mysql.com/downloads/installer/
2. 选择 "Developer Default" 或自定义安装 MySQL Server
3. 安装过程中设置 root 密码（请记住此密码）
4. 安装完成后确保 MySQL 服务正在运行：
   ```powershell
   Get-Service MySQL* | Select-Object Name, Status
   ```
5. 如果未启动：`Start-Service MySQL80`（服务名可能不同）

### 1.2 安装 Redis

Windows 上推荐使用 Memurai 或 WSL：

**方式 A：Memurai（Redis 的 Windows 原生版本）**
- 下载：https://www.memurai.com/get-memurai
- 安装后自动作为 Windows 服务运行

**方式 B：WSL2 + Redis**
```powershell
wsl --install
# 重启后在 WSL 中：
sudo apt update && sudo apt install redis-server -y
sudo service redis-server start
```

验证 Redis：
```powershell
# 如果安装了 redis-cli
redis-cli ping
# 应返回 PONG
```

### 1.3 Python 3.13

项目已使用 Python 3.13，确保 `python --version` 输出 3.13.x。

## 二、后端部署

### 2.1 安装后端依赖

```powershell
cd "C:\Users\zhuzhu\Desktop\zhuzhu Copilot\auth-server"
pip install -r requirements.txt
```

### 2.2 初始化数据库

```powershell
# 用你的 MySQL root 密码执行
mysql -u root -p < sql\init.sql
```

这会创建：
- 数据库 `zhuzhu_copilot_auth`
- 6 张表：users, points_log, orders, ip_registry, admins, system_config
- 默认管理员账号：`admin` / `admin123`

### 2.3 配置环境变量

```powershell
copy .env.example .env
notepad .env
```

编辑 `.env`，至少修改以下项：
```
MYSQL_PASSWORD=你的MySQL密码
JWT_SECRET=随机字符串（生产环境务必修改）
```

### 2.4 启动后端服务

```powershell
cd "C:\Users\zhuzhu\Desktop\zhuzhu Copilot\auth-server"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

看到以下输出表示启动成功：
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Redis 连接成功
```

验证：浏览器访问 `http://localhost:8000/` 应返回 `{"code":0,"message":"zhuzhu Copilot 认证服务运行中"}`

API 文档：`http://localhost:8000/docs`

### 2.5 后台运行（可选）

使用 PowerShell 后台运行：
```powershell
Start-Process python -ArgumentList "-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8000" -WorkingDirectory "C:\Users\zhuzhu\Desktop\zhuzhu Copilot\auth-server" -WindowStyle Hidden
```

### 2.6 云服务器（Linux）生产部署

本服务已部署至云服务器 `39.104.28.191`（Alibaba Cloud Linux 3），供公网访问。

**运行环境依赖**

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.11 | `/usr/bin/python3.11`，应用运行于 `/www/auth-server/.venv` 虚拟环境 |
| MySQL | 8.0+ | 数据库 `zhuzhu_copilot_auth`，默认管理员 `admin/admin123` |
| Redis | 7.0+ | 会话存储 / 限流锁 / 积分缓存 |
| Nginx | 1.x | 反向代理，对外暴露 HTTP/HTTPS |
| systemd | 内置 | 进程守护，`Restart=always` 自动拉起 |

**部署目录**：`/www/auth-server/`
**进程守护**：`systemctl` 服务 `auth-server.service`（`Restart=always`，开机自启）
**服务监听**：`0.0.0.0:8000`（uvicorn，2 workers）

```bash
# 查看服务状态
systemctl status auth-server
# 查看实时日志
journalctl -u auth-server -f
# 重启
systemctl restart auth-server
```

**公网访问（Nginx 反向代理）**

- HTTP：`http://39.104.28.191/`
- HTTPS（传输加密）：`https://39.104.28.191/`
- **域名入口（客户端使用）**：`https://chentian.dpdns.org/authapp/`
- 管理后台：`https://chentian.dpdns.org/authapp/static/admin/index.html`

Nginx 配置位于 `/etc/nginx/conf.d/auth-server.conf`（IP 站点）与 `/etc/nginx/conf.d/update-server.conf`（域名站点 `/authapp/` 反代块），通过 `server_name` 区分，互不影响。

> **WebSocket 关键点**：`/authapp/ws` 的 `proxy_pass` 必须写成
> `proxy_pass http://127.0.0.1:8000/ws$is_args$args;`
> 若省略 `$is_args$args`，`?token=...` 查询串会被丢弃，导致 WS 握手 400、客户端永远收不到实时推送。

> 说明：`chentian.dpdns.org` 站点的 SSL 证书也复用于本服务的 HTTPS 入口，为内部服务提供传输层加密。正式对外域名签发专属证书后，可将 `server_name` 与证书替换为正式域名。

**增量部署与验证脚本**

```powershell
python scripts/deploy_auth_changes.py            # 上传改动文件 + 重启 + 健康检查
python scripts/deploy_auth_changes.py --dry-run  # 只列出将上传的文件
python scripts/verify_login_e2e.py               # 走公网域名验证完整登录链路（13 项）
```

**数据库初始化**：首次部署执行 `sql/init.sql`（已建好 6 张表）。

## 三、客户端配置

### 3.1 安装客户端新依赖

```powershell
cd "C:\Users\zhuzhu\Desktop\zhuzhu Copilot"
pip install websockets>=12.0 httpx>=0.27
```

（已写入 `src/requirements.txt`）

### 3.2 服务端地址（固定，不可修改）

客户端**固定使用内置默认服务端地址**：

```
https://chentian.dpdns.org/authapp
```

该地址定义在 `src/zhuzhu_Copilot/core/auth_client.py` 的 `DEFAULT_AUTH_SERVER` 常量，
已取消「设置页自定义服务端地址」的能力——统一走官方服务端，避免用户误配导致登录失败。
历史版本写入的 `QSettings(auth_server_url)` 会在客户端启动时自动清理。

### 3.3 登录流程

1. 点击 AI 面板顶栏设置按钮右侧的"登录"按钮
2. 系统默认浏览器自动打开登录页
   `{server}/static/login/index.html?platform=zhuzhu-copilot&state=<一次性state>&version=<版本>&callback_port=18765`
3. 在浏览器中登录或注册账号
4. 登录成功后浏览器自动回调本地 `127.0.0.1:18765/callback` 传递 token
   （回调携带 state，客户端校验一致才接受）
5. 客户端收到 token 后自动保存，顶栏显示头像和用户名
6. WebSocket 自动连接，开始接收实时推送

## 四、管理员后台使用

### 4.1 访问后台

浏览器打开：`http://<服务端>:8000/static/admin/index.html`

默认账号：`admin` / `admin123`

### 4.2 功能说明

**用户管理：**
- 搜索用户（按用户名）
- 查看用户列表（积分、会员类型、状态、注册IP）
- 禁用/解封用户（禁用后客户端立即强制下线）
- 删除用户（软删除，客户端强制下线）
- 调整积分（正增负减，实时推送到客户端）

**订单管理：**
- 查看所有订单（支持按状态筛选）
- 确认到账：用户支付后，你在支付宝/微信确认收到款项，点击"确认到账"发放积分/会员
- 确认后客户端通过 WebSocket 收到 `order_paid` 通知

**收款码设置：**
- 上传支付宝收款码图片
- 上传微信收款码图片
- 上传后客户端支付对话框立即显示新的收款码

### 4.3 修改管理员密码

```powershell
mysql -u root -p
USE zhuzhu_copilot_auth;
-- 生成新密码的 bcrypt 哈希（在 Python 中执行）：
-- python -c "import bcrypt; print(bcrypt.hashpw(b'新密码', bcrypt.gensalt()).decode())"
UPDATE admins SET password_hash='新哈希值' WHERE username='admin';
```

## 五、支付流程（个人收款码模式）

由于没有对公账户，采用"用户扫码支付 → 管理员手动确认到账"模式：

1. 用户在客户端"会员与积分"页选择商品（Pro/Max会员或积分包）
2. 弹出用户协议，用户必须勾选"我已阅读并同意"才能继续
3. 选择支付方式（支付宝/微信），显示对应收款码
4. 用户扫码支付，订单状态为 `pending`
5. 管理员在支付宝/微信确认收到款项
6. 管理员登录后台 → 订单管理 → 找到对应订单 → 点击"确认到账"
7. 系统自动发放积分或开通会员
8. 客户端通过 WebSocket 收到到账通知，积分实时更新

**异常处理：** 如发现欺诈支付或异常行为，管理员可在用户管理中直接禁用该账号，客户端立即强制下线。

## 六、积分机制说明

| 项目 | 规则 |
|------|------|
| 新用户赠送 | 500 积分 |
| agens 模型消耗 | 0.03x 系数，每 1000 token ≈ 30 积分 |
| 积分归零 | 任务强制截断，输出末尾显示"您的积分不足，请接入其他AI服务" |
| Pro 会员 | 7 元/月，每月 1000 积分 |
| Max 会员 | 14 元/月，每月 2000 积分 |
| 积分包 | 150 积分 = 1 元（150/750/1500 三档） |
| 扣减方式 | Redis 原子操作（DECRBY），防止并发超扣 |

## 七、安全限制

| 限制 | 规则 |
|------|------|
| IP 注册 | 每个 IP 只能注册一个账号 |
| 登录失败 | 同一 IP 5 分钟内最多 5 次失败，超限锁定 5 分钟 |
| 密码存储 | bcrypt cost=12，不可逆 |
| 密保答案 | bcrypt 哈希存储 |
| 头像上传 | 最大 2MB，仅 jpg/png/gif，Pillow 验证文件头 |
| Token | JWT HS256，有效期 24 小时 |

## 八、WebSocket 消息类型

| 消息类型 | 方向 | 说明 |
|----------|------|------|
| `ping` | 客户端→服务端 | 心跳（30秒间隔） |
| `pong` | 服务端→客户端 | 心跳响应 |
| `points_update` | 服务端→客户端 | 积分变动推送 |
| `force_logout` | 服务端→客户端 | 强制下线（管理员禁用/删除） |
| `account_status` | 服务端→客户端 | 账号状态变更 |
| `order_paid` | 服务端→客户端 | 订单支付到账通知 |

## 九、文件清单

### 后端（auth-server/）
- `app/main.py` — FastAPI 入口，WebSocket 端点
- `app/config.py` — 配置管理
- `app/database.py` — MySQL 异步连接池
- `app/redis_client.py` — Redis 连接
- `app/api/auth.py` — 注册/登录/登出/头像
- `app/api/points.py` — 积分查询/消耗
- `app/api/shop.py` — 商品/订单/收款码
- `app/api/admin.py` — 管理员全部功能
- `app/api/agreement.py` — 用户协议
- `app/services/auth_service.py` — 认证业务逻辑
- `app/services/points_service.py` — 积分原子扣减
- `app/services/order_service.py` — 订单业务逻辑
- `app/services/ws_manager.py` — WebSocket 连接管理
- `app/core/security.py` — bcrypt/JWT/密保哈希
- `app/core/deps.py` — 依赖注入
- `sql/init.sql` — 数据库初始化脚本
- `requirements.txt` — 后端依赖
- `.env.example` — 配置模板

### Web 前端（auth-server/static/）
- `login/index.html` — 登录/注册页
- `login/agreement.html` — 用户协议
- `admin/index.html` — 管理员后台

### 客户端（src/zhuzhu_Copilot/）
- `core/auth_client.py` — 认证客户端（AuthClient + WebSocketClient + PointsConsumer）
- `core/agent_engine.py` — 集成积分消耗与登录门槛（已修改）
- `ui/agent_panel.py` — 顶栏用户区、会员页、强制下线弹窗（已修改）
- `requirements.txt` — 追加 websockets、httpx（已修改）

## 十、常见问题

**Q: 浏览器登录后没有自动回到应用？**
A: 检查本地 18765 端口是否被占用。客户端会在登录时启动临时回调服务器，如果端口被其他程序占用会失败。可在登录页手动复制 Token。

**Q: 客户端显示"连接服务器失败"？**
A: 客户端固定使用官方服务端 `https://chentian.dpdns.org/authapp`（不可自定义）。
请确认本机网络可访问该域名，以及服务端 `systemctl status auth-server` 为 active。

**Q: 积分没有实时更新？**
A: 检查 WebSocket 是否连接成功。网络不稳定时客户端会自动重连（指数退避），重连后积分会同步。

**Q: 管理员确认到账后用户积分没增加？**
A: 确认用户客户端在线且 WebSocket 已连接。离线用户下次登录时会从服务器拉取最新积分。

**Q: 如何重置用户密码？**
A: 当前版本通过密保问题找回密码的功能可在登录页扩展。管理员可直接在数据库中更新 password_hash 字段。

**Q: MySQL 连不上？**
A: 检查 `.env` 中的 MYSQL_HOST/PORT/USER/PASSWORD 是否正确，MySQL 服务是否运行，防火墙是否放行 3306 端口。
