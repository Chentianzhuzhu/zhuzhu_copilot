-- ============================================================
-- points_deduct.lua  积分原子扣减（防透支）
-- 用途：下单用积分抵扣、积分兑换
-- KEYS[1] = 余额键，  如 points:bal:{userId}
-- KEYS[2] = 流水键，  如 points:ledger:{userId}（ZSET）
-- ARGV[1] = 扣减积分
-- ARGV[2] = 业务单号（幂等比对 & 溯源）
-- ARGV[3] = 时间戳毫秒（ZSET score）
-- 返回  number >= 0 = 扣减后的余额
--       -1 = 余额不足
--       -2 = 该业务单号已扣减过（重复提交）
-- ------------------------------------------------------------
-- 一致性约定（重要）：
--   MySQL 的积分账本是唯一权威；Redis 余额是加速副本。
--   调用方必须先写 MySQL 流水（状态=已扣减），再调本脚本；
--   MySQL 写失败则不要调本脚本。对账任务每日比对
--   points:bal:* 与 MySQL 汇总，发现漂移以 MySQL 为准回写。
-- ============================================================
local dup = redis.call('ZSCORE', KEYS[2], ARGV[2])
if dup then
    return -2
end

local bal = tonumber(redis.call('GET', KEYS[1]) or '0')
local amt = tonumber(ARGV[1])

if bal < amt then
    return -1
end

redis.call('DECRBY', KEYS[1], amt)
redis.call('ZADD', KEYS[2], ARGV[3], ARGV[2])
-- 流水只保留最近 1000 条，防止ZSET无限增长
redis.call('ZREMRANGEBYRANK', KEYS[2], 0, -1001)

return bal - amt
