const authService = require('../services/authService');
const { User } = require('../models');

// 登录控制器
async function login(req, res) {
  try {
    const { username, password } = req.body;

    // 参数验证
    if (!username || !password) {
      return res.status(400).json({
        success: false,
        message: '用户名和密码不能为空'
      });
    }

    const result = await authService.login({ username, password });

    res.json({
      success: true,
      message: '登录成功',
      data: result
    });
  } catch (error) {
    res.status(401).json({
      success: false,
      message: error.message || '登录失败'
    });
  }
}

// 注册控制器
async function register(req, res) {
  try {
    const { username, email, password, nickname } = req.body;

    // 参数验证
    if (!username || !email || !password) {
      return res.status(400).json({
        success: false,
        message: '用户名、邮箱和密码不能为空'
      });
    }

    // 密码长度验证
    if (password.length < 6) {
      return res.status(400).json({
        success: false,
        message: '密码长度至少为6位'
      });
    }

    const user = await authService.register({ username, email, password, nickname });

    res.status(201).json({
      success: true,
      message: '注册成功',
      data: {
        id: user.id,
        username: user.username,
        email: user.email,
        nickname: user.nickname,
        role: user.role
      }
    });
  } catch (error) {
    res.status(400).json({
      success: false,
      message: error.message || '注册失败'
    });
  }
}

module.exports = { login, register };
