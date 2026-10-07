-- 增量迁移：释放已删除用户占用的用户名（2026-10-07）
-- 背景：users.username 是 UNIQUE 列，删除只把 status 置为 'deleted'（行保留），
--       导致该用户名被永久占用，之后无法再用同名建号。
-- 本迁移把历史上已删除行的用户名追加 '#del#<id>' 后缀释放原名。
-- 幂等：已带 '#del#' 的行不会被重复处理。
--
-- 执行：mysql -u<user> -p zhuzhu_copilot_auth < sql/migrate_20261007_free_deleted_username.sql

USE `zhuzhu_copilot_auth`;

-- 新用户名 = 原用户名截断到 (32 - 后缀长度) 后拼后缀，保证不超列宽且唯一。
-- 后缀形如 '#del#66'（id 唯一 → 结果必唯一）。
UPDATE `users`
SET `username` = CONCAT(
        LEFT(`username`, GREATEST(1, 32 - CHAR_LENGTH(CONCAT('#del#', `id`)))),
        '#del#', `id`)
WHERE `status` = 'deleted'
  AND `username` NOT LIKE '%#del#%';

-- 校验：应无任何 deleted 行仍占用「干净」用户名
SELECT COUNT(*) AS deleted_rows_still_holding_clean_name
FROM `users`
WHERE `status` = 'deleted' AND `username` NOT LIKE '%#del#%';