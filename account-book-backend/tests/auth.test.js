const request = require('supertest');
const app = require('../src/app');
const { User } = require('../src/models');
const jwt = require('jsonwebtoken');

const JWT_SECRET = process.env.JWT_SECRET || 'your_jwt_secret_key_change_in_production';

// 清除测试数据
beforeAll(async () => {
  await User.destroy({ where: {}, truncate: true });
});

describe('登录接口测试', () => {
  test('POST /api/auth/login - 成功登录', async () => {
    // 先注册一个用户
    const registerRes = await request(app)
      .post('/api/auth/register')
      .send({
        username: 'testuser',
        email: 'test@example.com',
        password: 'password123',
        nickname: '测试用户'
      });

    expect(registerRes.status).toBe(201);
    expect(registerRes.body.success).toBe(true);

    // 登录
    const loginRes = await request(app)
      .post('/api/auth/login')
      .send({
        username: 'testuser',
        password: 'password123'
      });

    expect(loginRes.status).toBe(200);
    expect(loginRes.body.success).toBe(true);
    expect(loginRes.body.data).toHaveProperty('token');
    expect(loginRes.body.data).toHaveProperty('user');
    expect(loginRes.body.data.user.username).toBe('testuser');
    expect(loginRes.body.data.user.password).toBeUndefined();
  });

  test('POST /api/auth/login - 用户名或密码错误', async () => {
    const loginRes = await request(app)
      .post('/api/auth/login')
      .send({
        username: 'testuser',
        password: 'wrongpassword'
      });

    expect(loginRes.status).toBe(401);
    expect(loginRes.body.success).toBe(false);
    expect(loginRes.body.message).toContain('错误');
  });

  test('POST /api/auth/login - 用户不存在', async () => {
    const loginRes = await request(app)
      .post('/api/auth/login')
      .send({
        username: 'nonexistent',
        password: 'password123'
      });

    expect(loginRes.status).toBe(401);
    expect(loginRes.body.success).toBe(false);
  });

  test('POST /api/auth/login - 缺少参数', async () => {
    const loginRes = await request(app)
      .post('/api/auth/login')
      .send({
        username: 'testuser'
      });

    expect(loginRes.status).toBe(400);
    expect(loginRes.body.success).toBe(false);
  });

  test('POST /api/auth/login - 空请求体', async () => {
    const loginRes = await request(app)
      .post('/api/auth/login')
      .send({});

    expect(loginRes.status).toBe(400);
    expect(loginRes.body.success).toBe(false);
  });
});

describe('注册接口测试', () => {
  test('POST /api/auth/register - 成功注册', async () => {
    const registerRes = await request(app)
      .post('/api/auth/register')
      .send({
        username: 'newuser',
        email: 'new@example.com',
        password: 'password123',
        nickname: '新用户'
      });

    expect(registerRes.status).toBe(201);
    expect(registerRes.body.success).toBe(true);
    expect(registerRes.body.data.username).toBe('newuser');
    expect(registerRes.body.data.email).toBe('new@example.com');
    expect(registerRes.body.data.password).toBeUndefined();
  });

  test('POST /api/auth/register - 用户名已存在', async () => {
    const registerRes = await request(app)
      .post('/api/auth/register')
      .send({
        username: 'newuser',
        email: 'another@example.com',
        password: 'password123'
      });

    expect(registerRes.status).toBe(400);
    expect(registerRes.body.success).toBe(false);
    expect(registerRes.body.message).toContain('存在');
  });

  test('POST /api/auth/register - 邮箱已存在', async () => {
    const registerRes = await request(app)
      .post('/api/auth/register')
      .send({
        username: 'anotheruser',
        email: 'new@example.com',
        password: 'password123'
      });

    expect(registerRes.status).toBe(400);
    expect(registerRes.body.success).toBe(false);
  });

  test('POST /api/auth/register - 密码长度不足', async () => {
    const registerRes = await request(app)
      .post('/api/auth/register')
      .send({
        username: 'shortpass',
        email: 'short@example.com',
        password: '12345'
      });

    expect(registerRes.status).toBe(400);
    expect(registerRes.body.success).toBe(false);
    expect(registerRes.body.message).toContain('6位');
  });

  test('POST /api/auth/register - 缺少必填字段', async () => {
    const registerRes = await request(app)
      .post('/api/auth/register')
      .send({
        username: 'noperfectuser'
      });

    expect(registerRes.status).toBe(400);
    expect(registerRes.body.success).toBe(false);
  });
});

describe('认证中间件测试', () => {
  let token;

  beforeAll(async () => {
    const registerRes = await request(app)
      .post('/api/auth/register')
      .send({
        username: 'authtest',
        email: 'authtest@example.com',
        password: 'password123'
      });
    
    const loginRes = await request(app)
      .post('/api/auth/login')
      .send({
        username: 'authtest',
        password: 'password123'
      });
    
    token = loginRes.body.data.token;
  });

  test('GET /api/auth/me - 有效 token', async () => {
    const meRes = await request(app)
      .get('/api/auth/me')
      .set('Authorization', `Bearer ${token}`);

    expect(meRes.status).toBe(200);
    expect(meRes.body.success).toBe(true);
    expect(meRes.body.data.username).toBe('authtest');
  });

  test('GET /api/auth/me - 无效 token', async () => {
    const meRes = await request(app)
      .get('/api/auth/me')
      .set('Authorization', 'Bearer invalid_token');

    expect(meRes.status).toBe(401);
    expect(meRes.body.success).toBe(false);
  });

  test('GET /api/auth/me - 无 token', async () => {
    const meRes = await request(app)
      .get('/api/auth/me');

    expect(meRes.status).toBe(401);
    expect(meRes.body.success).toBe(false);
  });

  test('GET /api/auth/me - 过期 token', async () => {
    const expiredToken = jwt.sign(
      { id: 1, username: 'authtest' },
      JWT_SECRET,
      { expiresIn: '-1s' }
    );

    const meRes = await request(app)
      .get('/api/auth/me')
      .set('Authorization', `Bearer ${expiredToken}`);

    expect(meRes.status).toBe(401);
    expect(meRes.body.success).toBe(false);
    expect(meRes.body.message).toContain('过期');
  });
});

describe('API 路由测试', () => {
  test('GET /health - 健康检查', async () => {
    const healthRes = await request(app).get('/health');
    expect(healthRes.status).toBe(200);
    expect(healthRes.body.status).toBe('ok');
  });

  test('GET /api/nonexistent - 404', async () => {
    const notFoundRes = await request(app).get('/api/nonexistent');
    expect(notFoundRes.status).toBe(404);
    expect(notFoundRes.body.success).toBe(false);
  });
});
