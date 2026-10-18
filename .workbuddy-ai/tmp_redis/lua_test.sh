#!/bin/bash
PW="M6Ig0fr4xCjGgiOGdM11u18Ppbrf5RiyeGbvrxYo"
R(){ redis-cli --no-auth-warning -a "$PW" "$@"; }
S=/etc/redis/scripts
echo "--- order_dedup ---"
R EVAL "$(cat $S/order_dedup.lua)" 1 idem:order:u1:rq1 600
R EVAL "$(cat $S/order_dedup.lua)" 1 idem:order:u1:rq1 600
R DEL idem:order:u1:rq1 >/dev/null
echo "--- stock_deduct ---"
R SET stock:sku:100 10
R EVAL "$(cat $S/stock_deduct.lua)" 1 stock:sku:100 {{qty}} 2>/dev/null || true
R EVAL "$(cat $S/stock_deduct.lua)" 1 stock:sku:100 3
R EVAL "$(cat $S/stock_deduct.lua)" 1 stock:sku:100 20
R EVAL "$(cat $S/stock_deduct.lua)" 1 stock:sku:NOPE 1
echo "--- points_deduct ---"
R SET points:bal:u1 1000
R EVAL "$(cat $S/points_deduct.lua)" 2 points:bal:u1 points:ledger:u1 100 ORD-001 1730000000000
R EVAL "$(cat $S/points_deduct.lua)" 2 points:bal:u1 points:ledger:u1 2000 ORD-002 1730000000001
R EVAL "$(cat $S/points_deduct.lua)" 2 points:bal:u1 points:ledger:u1 5 ORD-001 1730000000002
echo "ledger:"; R ZRANGE points:ledger:u1 0 -1 WITHSCORES
echo "--- points_credit ---"
R EVAL "$(cat $S/points_credit.lua)" 2 points:bal:u1 points:ledger:u1 50 ORD-003 1730000000003
R EVAL "$(cat $S/points_credit.lua)" 2 points:bal:u1 points:ledger:u1 50 ORD-003 1730000000004
echo "--- login_throttle ---"
for i in 1 2 3 4 5 6; do R EVAL "$(cat $S/login_throttle.lua)" 2 login:fail:admin login:lock:admin 5 900 1800; done
echo "--- batch_get ---"
R SET api:order:o1 "{\"id\":\"o1\"}"
R SET api:order:o2 "{\"id\":\"o2\"}"
R EVAL "$(cat $S/batch_get.lua)" 3 api:order:o1 api:order:o2 api:order:oMISS
echo "--- cleanup ---"
R DEL idem:order:u1:rq1 stock:sku:100 points:bal:u1 points:ledger:u1 login:fail:admin login:lock:admin api:order:o1 api:order:o2 >/dev/null
R DBSIZE
