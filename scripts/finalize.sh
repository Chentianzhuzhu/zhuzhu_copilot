#!/bin/bash
echo "=== 服务状态 ==="
systemctl is-active auth-server
echo "=== uptime ==="
systemctl show auth-server -p ActiveEnterTimestamp
echo "=== 清理测试用户/绑定 ==="
mysql -uroot -pwindows10 <<'EOF' 2>&1 | grep -v 'Using a password'
USE zhuzhu_copilot_auth;
DELETE FROM ip_registry WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'test_%' OR username LIKE 'lock_%' OR username='wstest');
DELETE FROM points_log WHERE user_id IN (SELECT id FROM users WHERE username LIKE 'test_%' OR username LIKE 'lock_%' OR username='wstest');
DELETE FROM users WHERE username LIKE 'test_%' OR username LIKE 'lock_%' OR username='wstest';
EOF
redis-cli keys 'login_*' | xargs -r redis-cli del >/dev/null 2>&1 || true
redis-cli keys 'auth_session:*' | xargs -r redis-cli del >/dev/null 2>&1 || true
redis-cli keys 'user_sessions:*' | xargs -r redis-cli del >/dev/null 2>&1 || true
redis-cli keys 'register_ip:*' | xargs -r redis-cli del >/dev/null 2>&1 || true
echo "=== 剩余用户 ==="
mysql -uroot -pwindows10 -e 'USE zhuzhu_copilot_auth; SELECT id,username,status FROM users;' 2>&1 | grep -v 'Using a password'
echo "=== 监听端口 ==="
ss -tlnp | grep -E ':8000|:80 |:443'