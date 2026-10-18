#!/bin/bash
OP='1m1YEh7YcwhK1MTAinu6E8gQnUCD'
PP='cCdRTRFUgc5qEe60HMjX6AejgAGz'
AP='pPBSXnAd9PA1VyHR0WCrbxgVyJC9'
S=/etc/redis/scripts
C() { U="$1"; P="$2"; shift 2; redis-cli --user "$U" --no-auth-warning -a "$P" "$@" 2>&1 | head -1; }

echo "############ api_order (订单域) ############"
echo "SET own key        : $(C api_order "$OP" SET order:o1 v1)"
echo "GET own key        : $(C api_order "$OP" GET order:o1)"
echo "EXPIRE/TTL         : $(C api_order "$OP" EXPIRE order:o1 300) / $(C api_order "$OP" TTL order:o1)"
echo "SET other domain   : $(C api_order "$OP" SET points:bal:u1 999)"
echo "GET session domain : $(C api_order "$OP" GET session:t1)"
echo "SCAN own keyspace  : $(C api_order "$OP" SCAN 0 MATCH 'order:*' COUNT 10 | head -c 60)"
echo "Lua stock deduct   : $(C api_order "$OP" EVAL "$(cat $S/stock_deduct.lua)" 1 stock:sku:7 2 | head -1)"
echo "CONFIG GET (deny)  : $(C api_order "$OP" CONFIG GET maxmemory)"

echo "############ api_points (积分域) ############"
echo "SET bal            : $(C api_points "$PP" SET points:bal:u1 1000)"
echo "Lua deduct 100     : $(C api_points "$PP" EVAL "$(cat $S/points_deduct.lua)" 2 points:bal:u1 points:ledger:u1 100 ORD-A 1730000000000)"
echo "Lua deduct dup     : $(C api_points "$PP" EVAL "$(cat $S/points_deduct.lua)" 2 points:bal:u1 points:ledger:u1 100 ORD-A 1730000000001)"
echo "Lua credit 50      : $(C api_points "$PP" EVAL "$(cat $S/points_credit.lua)" 2 points:bal:u1 points:ledger:u1 50 ORD-B 1730000000002)"
echo "SET order domain   : $(C api_points "$PP" SET order:o9 v9)"
echo "Lua on foreign key : $(C api_points "$PP" EVAL "$(cat $S/points_deduct.lua)" 2 order:o1 x 1 ORD-C 1730000000003)"

echo "############ api_auth (登录域) ############"
echo "SET session        : $(C api_auth "$AP" SET session:t1 '{"uid":42}' EX 7200)"
echo "GET session        : $(C api_auth "$AP" GET session:t1)"
echo "Lua throttle x3    : $(C api_auth "$AP" EVAL "$(cat $S/login_throttle.lua)" 2 login:fail:u5 login:lock:u5 3 900 1800) $(C api_auth "$AP" EVAL "$(cat $S/login_throttle.lua)" 2 login:fail:u5 login:lock:u5 3 900 1800) $(C api_auth "$AP" EVAL "$(cat $S/login_throttle.lua)" 2 login:fail:u5 login:lock:u5 3 900 1800)"
echo "DEL session        : $(C api_auth "$AP" DEL session:t1)"
echo "GET points domain  : $(C api_auth "$AP" GET points:bal:u1)"

echo "############ 全局禁用命令 ############"
echo "FLUSHALL           : $(C api_order "$OP" FLUSHALL)"
echo "KEYS               : $(C api_order "$OP" KEYS '*')"
echo "SHUTDOWN           : $(C api_auth "$AP" SHUTDOWN)"

echo "############ 清理测试数据 ############"
redis-cli --no-auth-warning -a "$(redis-cli --user api_order --no-auth-warning -a ${OP} INFO server >/dev/null 2>&1; echo M6Ig0fr4xCjGgiOGdM11u18Ppbrf5RiyeGbvrxYo)" --scan 2>/dev/null | head -0
echo "(cleanup skipped - 用 admin 会话清理)"
echo "TESTS_DONE"
