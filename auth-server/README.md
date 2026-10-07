# zhuzhu Copilot 认证服务

账号系统后端服务，基于 FastAPI + MySQL + Redis。

## 环境要求

- Python 3.13+
- MySQL 8.0+
- Redis 7.0+

## 快速启动

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 初始化数据库

```bash
# 登录 MySQL 后执行初始化脚本
mysql -u root -p < sql/init.sql
```

### 3. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入 MySQL 和 Redis 的连接信息
```

### 4. 启动服务

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## 默认管理员账号

- 用户名：`admin`
- 密码：`admin123`

**生产环境请务必修改默认密码！**

## 目录结构

```
auth-server/
├── app/
│   ├── main.py              # FastAPI 应用入口
│   ├── config.py            # 配置管理
│   ├── database.py          # MySQL 连接池
│   ├── redis_client.py      # Redis 连接
│   ├── models/              # 数据库模型
│   ├── schemas/             # Pydantic 模型
│   ├── api/                 # API 路由
│   ├── services/            # 业务逻辑
│   └── core/                # 安全、依赖注入
├── static/                  # 静态文件（登录页、管理后台、上传文件）
├── sql/
│   └── init.sql             # 数据库初始化脚本
├── requirements.txt         # Python 依赖
├── .env.example             # 配置示例
└── README.md
```

## API 文档

启动后访问：`http://localhost:8000/docs`（Swagger UI）
