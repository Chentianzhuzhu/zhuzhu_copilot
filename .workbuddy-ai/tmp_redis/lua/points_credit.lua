-- ============================================================
-- points_credit.lua  积分原子入账
-- 用途：订单完成返积分、签到、活动发放
-- KEYS[1] = 余额键，如 points:bal:{userId}
-- KEYS[2] = 流水键，如 points:ledger:{userId}（ZSET）
-- ARGV[1] = 增加积分
-- ARGV[2] = 业务单号
-- ARGV[3] = 时间戳毫秒
-- 返回 = 入账后的余额；若单号已存在则返回 -2（幂等，不重复加）
-- ============================================================
local dup = redis.call('ZSCORE', KEYS[2], ARGV[2])
if dup then
    return -2
end

local bal = redis.call('INCRBY', KEYS[1], ARGV[1])
redis.call('ZADD', KEYS[2], ARGV[3], ARGV[2])
redis.call('ZREMRANGEBYRANK', KEYS[2], 0, -1001)

return bal
