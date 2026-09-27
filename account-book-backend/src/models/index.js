const User = require('./User');
const sequelize = require('../config/database');

async function initModels() {
  await sequelize.authenticate();
  console.log('数据库连接成功');
  await sequelize.sync({ alter: true });
  console.log('数据表同步完成');
}

module.exports = { initModels, User };
