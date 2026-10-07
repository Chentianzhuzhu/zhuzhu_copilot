-- 增量迁移：每日签到（2026-10-07）
-- 用途：线上已有库补建 daily_checkin 表 + 签到积分配置。全部语句幂等，可重复执行。
-- 执行：mysql -u<user> -p zhuzhu_copilot_auth < sql/migrate_20261007_checkin.sql

USE `zhuzhu_copilot_auth`;

-- 1. 签到表
CREATE TABLE IF NOT EXISTS `daily_checkin` (
  `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
  `user_id` INT NOT NULL,
  `checkin_date` DATE NOT NULL COMMENT '签到日期（服务端本地日期，凌晨00:00 自动切换新的一天）',
  `points_awarded` INT NOT NULL DEFAULT 0 COMMENT '本次签到发放的积分（按签到时等级计算）',
  `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY `uk_user_date` (`user_id`, `checkin_date`),
  INDEX `idx_user_id` (`user_id`),
  INDEX `idx_date` (`checkin_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2. 签到积分配置（free 50 / pro 100 / max 200）
INSERT IGNORE INTO `system_config` (`config_key`, `config_value`) VALUES
('checkin_reward_free', '50'),
('checkin_reward_pro', '100'),
('checkin_reward_max', '200');