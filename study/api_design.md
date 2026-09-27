# 在线记账本 MVP - 后端接口与数据设计

## 一、数据模型设计

### 1.1 ER 图（文字描述）

```
用户 (User)
├── 1:N 账本 (AccountBook)
│   └── 1:N 账目 (Transaction)
├── 1:N 分类 (Category)
└── 1:N 用户分类关联 (UserCategory)
```

### 1.2 表结构定义

#### 用户表 (users)
| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| id | BIGINT | PK, AUTO_INCREMENT | 用户ID |
| username | VARCHAR(50) | UNIQUE, NOT NULL | 用户名 |
| password_hash | VARCHAR(255) | NOT NULL | 密码哈希 |
| avatar | VARCHAR(255) | NULL | 头像URL |
| created_at | DATETIME | DEFAULT NOW | 创建时间 |
| updated_at | DATETIME | DEFAULT NOW ON UPDATE | 更新时间 |

#### 账本表 (account_books)
| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| id | BIGINT | PK, AUTO_INCREMENT | 账本ID |
| user_id | BIGINT | FK → users.id, NOT NULL | 所属用户 |
| name | VARCHAR(50) | NOT NULL | 账本名称 |
| icon | VARCHAR(20) | NULL | 图标emoji |
| color | VARCHAR(7) | NULL | 主题色 |
| is_default | TINYINT(1) | DEFAULT 0 | 是否默认账本 |
| created_at | DATETIME | DEFAULT NOW | 创建时间 |
| updated_at | DATETIME | DEFAULT NOW ON UPDATE | 更新时间 |

#### 分类表 (categories)
| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| id | BIGINT | PK, AUTO_INCREMENT | 分类ID |
| user_id | BIGINT | FK → users.id, NOT NULL | 所属用户 |
| name | VARCHAR(30) | NOT NULL | 分类名称 |
| type | ENUM('income', 'expense') | NOT NULL | 收入/支出 |
| icon | VARCHAR(20) | NULL | 图标emoji |
| color | VARCHAR(7) | NULL | 颜色值 |
| sort_order | INT | DEFAULT 0 | 排序序号 |
| created_at | DATETIME | DEFAULT NOW | 创建时间 |
| updated_at | DATETIME | DEFAULT NOW ON UPDATE | 更新时间 |

#### 账目表 (transactions)
| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| id | BIGINT | PK, AUTO_INCREMENT | 账目ID |
| account_book_id | BIGINT | FK → account_books.id, NOT NULL | 所属账本 |
| category_id | BIGINT | FK → categories.id, NOT NULL | 所属分类 |
| amount | DECIMAL(10,2) | NOT NULL | 金额 |
| type | ENUM('income', 'expense') | NOT NULL | 收入/支出 |
| note | VARCHAR(200) | NULL | 备注 |
| transaction_date | DATE | NOT NULL | 交易日期 |
| created_at | DATETIME | DEFAULT NOW | 创建时间 |
| updated_at | DATETIME | DEFAULT NOW ON UPDATE | 更新时间 |

---

## 二、核心 API 接口设计

### 2.1 账目管理 API

#### 创建账目
```
POST /api/v1/transactions
Content-Type: application/json

Request Body:
{
  "account_book_id": 1,
  "category_id": 2,
  "amount": 35.00,
  "type": "expense",
  "note": "午餐",
  "transaction_date": "2024-01-15"
}

Response 200:
{
  "code": 0,
  "message": "success",
  "data": {
    "id": 1001,
    "account_book_id": 1,
    "category_id": 2,
    "amount": 35.00,
    "type": "expense",
    "note": "午餐",
    "transaction_date": "2024-01-15",
    "created_at": "2024-01-15T12:30:00Z"
  }
}
```

#### 查询账单列表
```
GET /api/v1/transactions?account_book_id=1&category_id=2&type=expense&date_start=2024-01-01&date_end=2024-01-31&page=1&page_size=20

Response 200:
{
  "code": 0,
  "message": "success",
  "data": {
    "list": [...],
    "total": 150,
    "page": 1,
    "page_size": 20
  }
}
```

#### 更新账目
```
PUT /api/v1/transactions/{id}
Content-Type: application/json

Request Body:
{
  "amount": 40.00,
  "note": "修改后的备注",
  "transaction_date": "2024-01-16"
}

Response 200:
{
  "code": 0,
  "message": "success"
}
```

#### 删除账目
```
DELETE /api/v1/transactions/{id}

Response 200:
{
  "code": 0,
  "message": "success"
}
```

---

### 2.2 分类管理 API

#### 获取分类列表
```
GET /api/v1/categories?type=expense

Response 200:
{
  "code": 0,
  "message": "success",
  "data": [
    {
      "id": 1,
      "name": "餐饮",
      "type": "expense",
      "icon": "🍔",
      "color": "#FF6B6B",
      "sort_order": 1
    }
  ]
}
```

#### 创建分类
```
POST /api/v1/categories
Content-Type: application/json

Request Body:
{
  "name": "健身",
  "type": "expense",
  "icon": "💪",
  "color": "#4ECDC4",
  "sort_order": 10
}

Response 200:
{
  "code": 0,
  "message": "success",
  "data": {
    "id": 15,
    "name": "健身",
    "type": "expense",
    "icon": "💪",
    "color": "#4ECDC4"
  }
}
```

#### 更新分类
```
PUT /api/v1/categories/{id}
Content-Type: application/json

Request Body:
{
  "name": "健身中心",
  "icon": "🏋️",
  "color": "#45B7D1"
}

Response 200:
{
  "code": 0,
  "message": "success"
}
```

#### 删除分类
```
DELETE /api/v1/categories/{id}

Response 200:
{
  "code": 0,
  "message": "success"
}
```

---

### 2.3 账本管理 API

#### 获取账本列表
```
GET /api/v1/account_books

Response 200:
{
  "code": 0,
  "message": "success",
  "data": [
    {
      "id": 1,
      "name": "日常账本",
      "icon": "📁",
      "color": "#2563EB",
      "is_default": 1,
      "transaction_count": 150,
      "created_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

#### 创建账本
```
POST /api/v1/account_books
Content-Type: application/json

Request Body:
{
  "name": "旅行账本",
  "icon": "✈️",
  "color": "#10B981"
}

Response 200:
{
  "code": 0,
  "message": "success",
  "data": {
    "id": 2,
    "name": "旅行账本",
    "icon": "✈️",
    "color": "#10B981"
  }
}
```

#### 设置默认账本
```
PUT /api/v1/account_books/{id}/default

Response 200:
{
  "code": 0,
  "message": "success"
}
```

---

### 2.4 统计接口 API

#### 月度收支统计
```
GET /api/v1/statistics/monthly?year_month=2024-01

Response 200:
{
  "code": 0,
  "message": "success",
  "data": {
    "year_month": "2024-01",
    "total_income": 3200.00,
    "total_expense": 1850.00,
    "balance": 1350.00,
    "category_breakdown": [
      {
        "category_id": 1,
        "category_name": "餐饮",
        "amount": 850.00,
        "percentage": 45.9
      }
    ]
  }
}
```

#### 收支趋势
```
GET /api/v1/statistics/trend?period=7d&type=expense

Response 200:
{
  "code": 0,
  "message": "success",
  "data": {
    "labels": ["01-09", "01-10", "01-11", "01-12", "01-13", "01-14", "01-15"],
    "values": [120, 85, 200, 150, 90, 110, 135]
  }
}
```

---

## 三、错误响应规范

### 3.1 标准错误响应
```json
{
  "code": 1001,
  "message": "参数错误：金额不能为空",
  "data": null
}
```

### 3.2 错误码定义

| 错误码 | 含义 |
|--------|------|
| 0 | 成功 |
| 1001 | 参数错误 |
| 1002 | 权限不足 |
| 1003 | 资源不存在 |
| 1004 | 重复操作 |
| 2001 | 系统内部错误 |

---

## 四、API 文档清单

| 模块 | 方法 | URL | 说明 |
|------|------|-----|------|
| 账目 | POST | /api/v1/transactions | 创建账目 |
| 账目 | GET | /api/v1/transactions | 查询账目列表 |
| 账目 | PUT | /api/v1/transactions/{id} | 更新账目 |
| 账目 | DELETE | /api/v1/transactions/{id} | 删除账目 |
| 分类 | GET | /api/v1/categories | 获取分类列表 |
| 分类 | POST | /api/v1/categories | 创建分类 |
| 分类 | PUT | /api/v1/categories/{id} | 更新分类 |
| 分类 | DELETE | /api/v1/categories/{id} | 删除分类 |
| 账本 | GET | /api/v1/account_books | 获取账本列表 |
| 账本 | POST | /api/v1/account_books | 创建账本 |
| 账本 | PUT | /api/v1/account_books/{id}/default | 设置默认账本 |
| 统计 | GET | /api/v1/statistics/monthly | 月度统计 |
| 统计 | GET | /api/v1/statistics/trend | 趋势统计 |

---

## 五、数据库索引建议

```sql
-- 账目表索引
CREATE INDEX idx_transactions_account_book ON transactions(account_book_id);
CREATE INDEX idx_transactions_category ON transactions(category_id);
CREATE INDEX idx_transactions_date ON transactions(transaction_date);
CREATE INDEX idx_transactions_type ON transactions(type);

-- 分类表索引
CREATE INDEX idx_categories_user_type ON categories(user_id, type);

-- 账本表索引
CREATE INDEX idx_account_books_user ON account_books(user_id);
```

---

*文档版本：v1.0*
*更新日期：2026-01-15*
