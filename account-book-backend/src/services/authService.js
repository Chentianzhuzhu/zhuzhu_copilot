const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const User = require('../models/User');

const JWT_SECRET = process.env.JWT_SECRET || 'your_jwt_secret_key_change_in_production';
const JWT_EXPIRES_IN = process.env.JWT_EXPIRES_IN || '7d';

// 用户注册
async function register({ username, email, password, nickname }) {
  // 检查用户名是否已存在
  const existingUser = await User.findOne({ where: { username } });
  if (existingUser) {
    throw new Error('用户名已存在');
  }

  // 检查邮箱是否已存在
  const existingEmail = await User.findOne({ where: { email } });
  if (existingEmail) {
    throw new Error('邮箱已被注册');
  }

  // 密码加密
  const hashedPassword = await bcrypt.hash(password, 10);

  // 创建用户
  const user = await User.create({
    username,
    email,
    password: hashedPassword,
    nickname: nickname || username
  });

  return user;
}

// 用户登录
async function login({ username, password }) {
  // 查找用户
  const user = await User.findOne({
    where: { username }
  });

  if (!user) {
    throw new Error('用户名或密码错误');
  }

  // 检查账户状态
  if (user.status !== 'active') {
    throw new Error('账户已被禁用');
  }

  // 验证密码
  const isValidPassword = await bcrypt.compare(password, user.password);
  if (!isValidPassword) {
    throw new Error('用户名或密码错误');
  }

  // 更新最后登录时间
  await User.update(
    { last_login_at: new Date() },
    { where: { id: user.id } }
  );

  // 生成 JWT token
  const token = jwt.sign(
    { id: user.id, username: user.username, role: user.role },
    JWT_SECRET,
    { expiresIn: JWT_EXPIRES_IN }
  );

  // 返回用户信息（不包含密码）
  const { password: _, ...userWithoutPassword } = user.toJSON();
  
  return {
    user: userWithoutPassword,
    token
  };
}

// 根据 ID 获取用户信息
async function getUserById(id) {
  const user = await User.findByPk(id);
  if (!user) return null;
  
  const { password: _, ...userWithoutPassword } = user.toJSON();
  return userWithoutPassword;
}

module.exports = { register, login, getUserById };
