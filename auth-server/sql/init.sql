-- zhuzhu Copilot 账号系统数据库初始化脚本
-- 执行前请确保 MySQL 版本 >= 8.0

CREATE DATABASE IF NOT EXISTS `zhuzhu_copilot_auth` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE `zhuzhu_copilot_auth`;

-- 用户表
CREATE TABLE IF NOT EXISTS `users` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `username` VARCHAR(32) UNIQUE NOT NULL,
  `password_hash` VARCHAR(255) NOT NULL,
  `security_question` VARCHAR(128) NOT NULL,
  `security_answer` VARCHAR(255) NOT NULL COMMENT '密保答案bcrypt哈希',
  `avatar` VARCHAR(512) DEFAULT '',
  `points` INT DEFAULT 500,
  `membership_type` ENUM('free','pro','max') DEFAULT 'free',
  `membership_expire` DATETIME NULL,
  `status` ENUM('active','disabled','deleted') DEFAULT 'active',
  `register_ip` VARCHAR(45),
  `login_fail_count` INT DEFAULT 0 COMMENT '连续登录失败次数（服务端持久化，配合Redis限流）',
  `last_login_at` DATETIME NULL COMMENT '最近一次登录成功时间',
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX `idx_username` (`username`),
  INDEX `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 每日签到表（user_id + checkin_date 唯一，保证一天只能签一次）
CREATE TABLE IF NOT EXISTS `daily_checkin` (
  `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
  `user_id` INT NOT NULL,
  `checkin_date` DATE NOT NULL COMMENT '签到日期（服务端本地日期，凌晨00:00 自动切换新的一天）',
  `points_awarded` INT NOT NULL DEFAULT 0 COMMENT '本次签到发放的积分（按下单时等级计算）',
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY `uk_user_date` (`user_id`, `checkin_date`),
  INDEX `idx_user_id` (`user_id`),
  INDEX `idx_date` (`checkin_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 积分流水表
CREATE TABLE IF NOT EXISTS `points_log` (
  `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
  `user_id` INT NOT NULL,
  `change_amount` INT NOT NULL,
  `balance_after` INT NOT NULL,
  `reason` VARCHAR(64),
  `detail` VARCHAR(256),
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX `idx_user_id` (`user_id`),
  INDEX `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 订单表
CREATE TABLE IF NOT EXISTS `orders` (
  `id` VARCHAR(32) PRIMARY KEY,
  `user_id` INT NOT NULL,
  `order_type` ENUM('membership','points') NOT NULL,
  `product_id` VARCHAR(32) NOT NULL,
  `amount` DECIMAL(10,2) NOT NULL,
  `points_amount` INT,
  `pay_method` ENUM('alipay','wechat') NOT NULL,
  `status` ENUM('pending','paid','cancelled','refunded') DEFAULT 'pending',
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  `paid_at` DATETIME NULL,
  INDEX `idx_user_id` (`user_id`),
  INDEX `idx_status` (`status`),
  INDEX `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- IP注册表
CREATE TABLE IF NOT EXISTS `ip_registry` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `ip` VARCHAR(45) UNIQUE NOT NULL,
  `user_id` INT NOT NULL,
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX `idx_ip` (`ip`),
  INDEX `idx_user_id` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 管理员表
CREATE TABLE IF NOT EXISTS `admins` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `username` VARCHAR(32) UNIQUE NOT NULL,
  `password_hash` VARCHAR(255) NOT NULL,
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 系统配置表
CREATE TABLE IF NOT EXISTS `system_config` (
  `config_key` VARCHAR(64) PRIMARY KEY,
  `config_value` TEXT,
  `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 插入默认管理员 zhutianlaing / windows10 (bcrypt cost=12)
INSERT INTO `admins` (`username`, `password_hash`) VALUES
('zhutianlaing', '$2b$12$YMqahEQKjY6u3cGEyRnMROnWCtEhSVuatn/rmaCLyot4M0abzy3SK');

-- 插入默认系统配置
INSERT INTO `system_config` (`config_key`, `config_value`) VALUES
('alipay_qrcode', ''),
('wechat_qrcode', ''),
('announcement', ''),
('announcement_enabled', '1');

-- 每日签到积分配置（按等级；缺省值见app/services/checkin_service.DEFAULT_CHECKIN_REWARDS）
INSERT IGNORE INTO `system_config` (`config_key`, `config_value`) VALUES
('checkin_reward_free', '50'),
('checkin_reward_pro', '100'),
('checkin_reward_max', '200');
