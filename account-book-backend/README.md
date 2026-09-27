# 在线记账本后端服务

基于 Node.js + Express + SQLite + JWT 的后端服务，实现用户登录、注册等核心功能。

## 技术栈

- **运行环境**: Node.js 18+
- **Web 框架**: Express.js
- **ORM**: Sequelize
- **数据库**: SQLite
- **认证**: JWT (jsonwebtoken)
- **密码加密**: bcryptjs
- **测试**: Jest + Supertest

## 项目结构

```
account-book-backend/
├── src/
│   ├── app.js              # 应用入口
│   ├── config/
│   │   └── database.js     # 数据库配置
│   ├── controllers/
│   │   └── authController.js  # 认证控制器
│   ├── middleware/
│   │   └── auth.js         # JWT 认证中间件
│   ├── models/
│   │   ├── index.js        # 模型初始化
│   │   └── User.js         # 用户模型
│   ├── routes/
│   │   └── auth.js         # 认证路由
│   ├── services/
│   │   └── authService.js  # 认证业务逻辑
│   └── utils/              # 工具函数
├── tests/
│   └── auth.test.js        # 测试用例
├── .env.example            # 环境变量模板
├── package.json
└── README.md
```

## 快速开始

### 1. 安装依赖

```bash
cd account-book-backend
npm install
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 修改 .env 中的配置，特别是 JWT_SECRET
```

### 3. 启动服务

```bash
npm start
```

开发模式（热重载）：

```bash
npm run dev
```

### 4. 运行测试

```bash
npm test
```

## API 接口

### 注册接口

```
POST /api/auth/register
Content-Type: application/json

{
  "username": "string",  // 用户名，必填，唯一
  "email": "string",     // 邮箱，必填，唯一
  "password": "string",  // 密码，必填，至少6位
  "nickname": "string"   // 昵称，可选
}
```

**响应示例：**

```json
{
  "success": true,
  "message": "注册成功",
  "data": {
    "id": 1,
    "username": "testuser",
    "email": "test@example.com",
    "nickname": "测试用户",
    "role": "user"
  }
}
```

### 登录接口

```
POST /api/auth/login
Content-Type: application/json

{
  "username": "string",  // 用户名
  "password": "string"   // 密码
}
```

**响应示例：**

```json
{
  "success": true,
  "message": "登录成功",
  "data": {
    "user": {
      "id": 1,
      "username": "testuser",
      "email": "test@example.com",
      "nickname": "测试用户",
      "role": "user",
      "status": "active",
      "created_at": "2024-01-01T00:00:00.000Z",
      "last_login_at": "2024-01-01T00:00:00.000Z"
    },
    "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
  }
}
```

### 获取当前用户信息

```
GET /api/auth/me
Authorization: Bearer <token>
```

**响应示例：**

```json
{
  "success": true,
  "data": {
    "id": 1,
    "username": "testuser",
    "email": "test@example.com",
    ...
  }
}
```

## 安全说明

1. **密码加密**: 使用 bcryptjs 进行密码哈希，密钥轮次为 10
2. **JWT 认证**: Token 有效期默认 7 天，通过 Authorization header 传递
3. **输入验证**: 对用户输入进行基本校验，防止 SQL 注入等攻击
4. **敏感信息**: 返回给用户的数据不包含密码字段

## 测试覆盖

运行测试：

```bash
npm test
```

查看覆盖率报告：

```bash
npm test -- --coverage
```
