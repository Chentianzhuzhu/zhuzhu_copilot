#!/bin/bash
# ============================================================
# Redis 缓存节点健康检查 (122.228.216.138)
# 用法: bash redis_healthcheck.sh
# 退出码: 0 = 健康, 1 = 异常
# ============================================================
set -uo pipefail

REDIS_CLI_ARGS=(-h 127.0.0.1 -p 6379)
PASS_FILE=/etc/redis_admin_pw
STATUS=0
warn() { echo "  [WARN ] $1"; STATUS=1; }
ok()   { echo "  [ OK  ] $1"; }

if [ -r "$PASS_FILE" ]; then
  ADMIN_PASS=$(cat "$PASS_FILE")
else
  read -rsp "Redis admin password: " ADMIN_PASS; echo
fi
R() { redis-cli "${REDIS_CLI_ARGS[@]}" --no-auth-warning -a "$ADMIN_PASS" "$@" 2>/dev/null; }

echo "=============== Redis 健康检查 $(date '+%F %T') ==============="

echo "[1] 服务状态"
if systemctl is-active --quiet redis-server; then ok "redis-server active"; else warn "redis-server NOT active"; fi
if systemctl is-enabled --quiet redis-server; then ok "开机自启 enabled"; else warn "未设置开机自启"; fi

echo "[2] 连通与认证"
if [ "$(R PING)" = "PONG" ]; then ok "PING + AUTH 正常"; else warn "PING 失败"; fi

echo "[3] 内存"
max=$(R CONFIG GET maxmemory | paste -sd' ' | awk '{print $2}')
used=$(R INFO memory | grep -E '^used_memory_human:' | cut -d: -f2 | tr -d '\r')
policy=$(R CONFIG GET maxmemory-policy | paste -sd' ' | awk '{print $2}')
frag=$(R INFO memory | grep -E '^mem_fragmentation_ratio:' | cut -d: -f2 | tr -d '\r')
echo "  已用: $used / 上限: $((max/1024/1024)) MB, 策略: $policy, 碎片率: $frag"
usedb=$(R INFO memory | grep -E '^used_memory_dataset:' | cut -d: -f2 | tr -d '\r')
if [ "$max" -gt 0 ] && [ "${usedb:-0}" -gt $((max*9/10)) ]; then
  warn "内存接近上限（数据量 ${usedb} / ${max}），请考虑扩容或检查 TTL"
else
  ok "内存水位正常"
fi

echo "[4] 命中率（低命中率说明缓存策略需优化）"
hits=$(R INFO stats | grep -E '^keyspace_hits:' | cut -d: -f2 | tr -d '\r')
miss=$(R INFO stats | grep -E '^keyspace_misses:' | cut -d: -f2 | tr -d '\r')
total=$((hits+miss))
if [ "$total" -gt 0 ]; then
  rate=$(awk -v h="$hits" -v t="$total" 'BEGIN{printf "%.1f", h*100/t}')
  echo "  命中 ${hits} / 未命中 ${miss} => 命中率 ${rate}%"
  if awk -v r="$rate" 'BEGIN{exit !(r<70)}'; then warn "缓存命中率低于 70%，检查 TTL 与缓存键设计"; else ok "命中率可接受"; fi
else
  echo "  暂无读写样本（服务刚启动或未接入）"
fi

echo "[5] 连接与积压"
clients=$(R INFO clients | grep -E '^connected_clients:' | cut -d: -f2 | tr -d '\r')
blocked=$(R INFO clients | grep -E '^blocked_clients:' | cut -d: -f2 | tr -d '\r')
echo "  当前连接: ${clients}, 阻塞中: ${blocked}"
if [ "${clients:-0}" -gt 5000 ]; then warn "连接数过高，检查是否使用连接池"; else ok "连接数正常"; fi

echo "[6] 淘汰与过期"
evicted=$(R INFO stats | grep -E '^evicted_keys:' | cut -d: -f2 | tr -d '\r')
expired=$(R INFO stats | grep -E '^expired_keys:' | cut -d: -f2 | tr -d '\r')
echo "  已淘汰: ${evicted}, 已过期: ${expired}"
if [ "${evicted:-0}" -gt 10000 ]; then warn "淘汰量过大，内存可能不足"; else ok "淘汰可控"; fi

echo "[7] 慢查询"
slow=$(R SLOWLOG LEN)
echo "  慢查询记录: ${slow}"
if [ "${slow:-0}" -gt 20 ]; then warn "慢查询过多，执行 SLOWLOG GET 排查"; fi

echo "[8] ACL 账号"
for u in api_order api_points api_auth; do
  if R ACL LIST | grep -q "^user $u "; then ok "$u 存在"; else warn "$u 缺失"; fi
done
if redis-cli "${REDIS_CLI_ARGS[@]}" PING 2>/dev/null | grep -q NOAUTH; then ok "未认证连接被拒绝"; else warn "存在无密码访问风险！"; fi

echo "[9] 键数量"
echo "  DBSIZE: $(R DBSIZE)"

echo "=============== 结束（STATUS=$STATUS）================"
exit $STATUS
