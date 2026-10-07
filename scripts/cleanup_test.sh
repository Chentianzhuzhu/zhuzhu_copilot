#!/bin/bash
echo "==> 清理 Redis 注册/IP/登录锁定键"
redis-cli keys 'register_ip:*' | xargs -r redis-cli del >/dev/null 2>&1 || true
redis-cli keys 'login_*' | xargs -r redis-cli del >/dev/null 2>&1 || true
redis-cli keys 'auth_session:*' | xargs -r redis-cli del >/dev/null 2>&1 || true
redis-cli keys 'user_sessions:*' | xargs -r redis-cli del >/dev/null 2>&1 || true
redis-cli keys 'points_cache:*' | xargs -r redis-cli del >/dev/null 2>&1 || true

echo "==> 清理测试用户及IP注册记录"
mysql -uroot -p'windows10' <<'EOF' 2>&1 | grep -v 'Using a password'
USE zhuzhu_copilot_auth;
DELETE FROM ip_registry WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'test_%' OR username LIKE 'lock_%');
DELETE FROM points_log WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'test_%' OR username LIKE 'lock_%');
DELETE FROM users WHERE username LIKE 'test_%' OR username LIKE 'lock_%';
EOF
echo "cleanup done"