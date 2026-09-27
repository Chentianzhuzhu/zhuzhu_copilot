const express = require('express');
const authController = require('../controllers/authController');

const router = express.Router();

// 登录路由
router.post('/login', authController.login);

// 注册路由
router.post('/register', authController.register);

// 获取当前用户信息
router.get('/me', async (req, res) => {
  res.json({
    success: true,
    data: req.user
  });
});

module.exports = router;
