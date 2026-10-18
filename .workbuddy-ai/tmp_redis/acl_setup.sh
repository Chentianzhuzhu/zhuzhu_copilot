#!/bin/bash
# Add ACL file support + per-service accounts (orders / points / login)
set -euo pipefail

ORDER_PW='1m1YEh7YcwhK1MTAinu6E8gQnUCD'
POINTS_PW='cCdRTRFUgc5qEe60HMjX6AejgAGz'
AUTH_PW='pPBSXnAd9PA1VyHR0WCrbxgVyJC9'
ADMIN_PW='M6Ig0fr4xCjGgiOGdM11u18Ppbrf5RiyeGbvrxYo'
ACLFILE='/var/lib/redis/users.acl'

echo "=========== [1/5] Enable ACL file persistence ==========="
if grep -q '^aclfile ' /etc/redis/redis.conf; then
  echo "aclfile already set"
else
  # NOTE: aclfile must live in /var/lib/redis - the systemd unit makes
  # /etc/redis read-only (ProtectSystem=strict), so ACL SAVE would fail there.
  printf '\n################## ACL ##################\n# ACL persistence. Must be under /var/lib/redis: ProtectSystem=strict\n# makes /etc/redis read-only, so ACL SAVE cannot write there.\naclfile %s\n' "$ACLFILE" >> /etc/redis/redis.conf
  echo "aclfile -> $ACLFILE"
fi

touch "$ACLFILE"
chown redis:redis "$ACLFILE"
chmod 640 "$ACLFILE"

echo "=========== [2/5] Restart with aclfile ==========="
systemctl restart redis-server
sleep 2
systemctl is-active redis-server

echo "=========== [3/5] Create service users ==========="
R() { redis-cli --no-auth-warning -a "$ADMIN_PW" "$@"; }

# api_order: 订单域 —— 幂等键、库存、订单缓存、接口限流
R ACL SETUSER api_order on ">$ORDER_PW" \
  '~order:*' '~stock:*' '~idem:*' '~rl:order:*' \
  +@all -@dangerous -@admin -@pubsub -@blocking +info

# api_points: 积分域 —— 余额、流水、限流
R ACL SETUSER api_points on ">$POINTS_PW" \
  '~points:*' '~rl:points:*' \
  +@all -@dangerous -@admin -@pubsub -@blocking +info

# api_auth: 登录域 —— 会话、令牌、失败计数、锁定标记、限流
R ACL SETUSER api_auth on ">$AUTH_PW" \
  '~session:*' '~tok:*' '~login:*' '~rl:login:*' \
  +@all -@dangerous -@admin -@pubsub -@blocking +info

echo "created: api_order, api_points, api_auth"
echo "=========== [4/5] Persist ACL ==========="
R ACL SAVE
ls -l "$ACLFILE"
echo "=========== [5/5] Verify ==========="
R ACL LIST
R ACL GETUSER api_order | head -6
echo "ACL_DONE"
