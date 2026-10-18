-- ============================================================
-- stock_deduct.lua  订单库存原子扣减（防超卖）
-- 用途：秒杀/下单时的库存预扣
-- KEYS[1] = 库存键，如 stock:{skuId}
-- ARGV[1] = 扣减数量
-- ARGV[2] = 流水号（写回撤单用，返回给调用方）
-- 返回  >=0 = 剩余库存（扣减成功）
--       -1  = 库存不足
--       -2  = 库存键不存在（说明未预热，应走DB路径）
-- ------------------------------------------------------------
-- 关键点：GET 与 DECRBY 在同一次Lua调用内串行执行，
-- 不会出现"两个请求同时读到库存=1都扣成功"的超卖竞态。
-- 缓存与DB不一致时 Redis 是权威值（预热阶段保证），
-- 撤单/退款时用返回的剩余库存反推，或用 stock_restore.lua 回滚。
-- ============================================================
local exist = redis.call('EXISTS', KEYS[1])
if exist == 0 then
    return -2
end

local n = tonumber(redis.call('GET', KEYS[1]) or '0')
local amt = tonumber(ARGV[1])

if n < amt then
    return -1
end

local left = redis.call('DECRBY', KEYS[1], amt)
return left
