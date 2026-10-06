-- WinAppMigrator 更新服务器数据库初始化（MySQL 8+）
-- 执行：mysql -u root -p < schema.sql
CREATE DATABASE IF NOT EXISTS winapp_update DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE winapp_update;

CREATE TABLE IF NOT EXISTS app_version (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    version      VARCHAR(32)  NOT NULL UNIQUE COMMENT '语义化版本号 1.2.0',
    file_name    VARCHAR(255) NOT NULL COMMENT '安装包原始文件名',
    file_path    VARCHAR(512) NOT NULL COMMENT '相对 uploads 的存储路径 pkg/xxx',
    file_size    BIGINT       NOT NULL COMMENT '字节数',
    md5          VARCHAR(32)  COMMENT '文件 MD5',
    notes        TEXT         COMMENT '更新说明',
    force_update TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否强制更新',
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE = InnoDB COMMENT ='已发布版本';

CREATE TABLE IF NOT EXISTS download_log (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    version_id   BIGINT       NOT NULL COMMENT '关联 app_version.id',
    ip           VARCHAR(64)  COMMENT '下载方 IP',
    user_agent   VARCHAR(255) COMMENT '客户端 UA',
    created_date DATE         NOT NULL COMMENT '下载日期（趋势统计按此分组）',
    KEY idx_log_version (version_id),
    KEY idx_log_date (created_date)
) ENGINE = InnoDB COMMENT ='下载记录';

CREATE TABLE IF NOT EXISTS site_content (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    ckey         VARCHAR(32) NOT NULL UNIQUE COMMENT '内容键，固定 site',
    content_json TEXT        NOT NULL COMMENT '官网内容 JSON',
    updated_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE = InnoDB COMMENT ='官网内容（后台可编辑）';

CREATE TABLE IF NOT EXISTS feedback (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    ip           VARCHAR(64)  COMMENT '提交者 IP（限流依据）',
    content      TEXT         NOT NULL COMMENT '反馈内容（≤2000 字）',
    contact      VARCHAR(255) COMMENT '联系方式（邮箱/QQ/微信等，可选）',
    status       VARCHAR(16)  NOT NULL DEFAULT 'pending' COMMENT '状态 pending/replied/resolved',
    reply        TEXT         COMMENT '管理员回复',
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '提交时间（服务器端）',
    replied_at   DATETIME     NULL COMMENT '回复时间',
    query_token  VARCHAR(64)  NULL COMMENT '查询凭证（随机64位hex，防止ID遍历）',
    KEY idx_feedback_ip_time (ip, created_at),
    UNIQUE KEY uk_feedback_query_token (query_token)
) ENGINE = InnoDB COMMENT ='用户反馈';
