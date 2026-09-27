const jwt = require('jsonwebtoken');
const { getUserById } = require('../services/authService');

const JWT_SECRET = process.env.JWT_SECRET || 'your_jwt_secret_key_change_in_production';

// JWT 认证中间件
async function authenticate(req, res, next) {
  try {
    let token = null;

    // 从 Authorization header 获取 token
    if (req.headers.authorization && req.headers.authorization.startsWith('Bearer')) {
      token = req.headers.authorization.split(' ')[1];
    }

    if (!token) {
      return res.status(401).json({
        success: false,
        message: '未提供认证令牌'
      });
    }

    // 验证 token
    const decoded = jwt.verify(token, JWT_SECRET);

    // 获取用户信息
    const user = await getUserById(decoded.id);
    if (!user) {
      return res.status(401).json({
        success: false,
        message: '用户不存在'
      });
    }

    // 将用户信息附加到请求对象
    req.user = user;
    next();
  } catch (error) {
    if (error.name === 'JsonWebTokenError') {
      return res.status(401).json({
        success: false,
        message: '无效的认证令牌'
      });
    }
    if (error.name === 'TokenExpiredError') {
      return res.status(401).json({
        success: false,
        message: '认证令牌已过期'
      });
    }
    return res.status(500).json({
      success: false,
      message: '服务器错误'
    });
  }
}

// 可选认证中间件（token 存在则验证，不存在也放行）
function optionalAuthenticate(req, res, next) {
  try {
    let token = null;

    if (req.headers.authorization && req.headers.authorization.startsWith('Bearer')) {
      token = req.headers.authorization.split(' ')[1];
    }

    if (token) {
      const decoded = jwt.verify(token, JWT_SECRET);
      req.user = decoded;
    }

    next();
  } catch (error) {
    // token 无效时忽略，继续请求
    next();
  }
}

module.exports = { authenticate, optionalAuthenticate };
