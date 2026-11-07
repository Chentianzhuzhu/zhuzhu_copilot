#!/bin/bash
# pay-server 一键部署脚本：jar + auth-server 补丁 + 建库建号 + systemd + nginx
#
# 用法：
#   export SSHPASS_PASSWORD='服务器密码'
#   export STRIPE_SECRET_KEY='sk_live_...'          # 必填
#   export STRIPE_WEBHOOK_SECRET='whsec_...'        # 首次部署可留空（创建 webhook 后补）
#   bash scripts/deploy_pay_server.sh
#
# 安全约定：SSH 密码只从 SSHPASS_PASSWORD 环境变量读取，不落盘、不进 git。
set -euo pipefail

HOST="39.104.28.191"
USER="root"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JAR="$ROOT/pay-server/target/pay-server.jar"
AUTH_DIR="$ROOT/auth-server"

# ---------- 前置检查 ----------
[ -f "$JAR" ] || { echo "缺少 $JAR，请先 mvn package"; exit 1; }
: "${SSHPASS_PASSWORD:?缺少 SSHPASS_PASSWORD}"
: "${STRIPE_SECRET_KEY:?缺少 STRIPE_SECRET_KEY}"

AUTH_INTERNAL_TOKEN="${AUTH_INTERNAL_TOKEN:-$(openssl rand -hex 32)}"

# ---------- SSH 通道（OpenSSH + askpass）----------
AP="$(mktemp)"
cat > "$AP" <<'EOF'
#!/bin/sh
printf '%s\n' "$SSHPASS_PASSWORD"
EOF
chmod +x "$AP"
export SSH_ASKPASS="$AP" SSH_ASKPASS_REQUIRE=force DISPLAY=:0
SSH="ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 $USER@$HOST"
SCP="scp -o StrictHostKeyChecking=accept-new"

cleanup() { rm -f "$AP"; }
trap cleanup EXIT

run() { echo "[SSH] $1"; $SSH "$1"; }
put() { echo "[SCP] $1 -> $2"; if $SCP "$1" "$USER@$HOST:$2"; then :; else echo "上传失败: $1"; exit 1; fi; }

# ---------- 1. auth-server 补丁 ----------
put "$AUTH_DIR/app/api/internal.py"          /www/auth-server/app/api/internal.py
put "$AUTH_DIR/app/services/order_service.py" /www/auth-server/app/services/order_service.py
put "$AUTH_DIR/app/config.py"                /www/auth-server/app/config.py
put "$AUTH_DIR/app/main.py"                  /www/auth-server/app/main.py
put "$AUTH_DIR/app/schemas/__init__.py"      /www/auth-server/app/schemas/__init__.py

# ---------- 2. 建库建号（幂等）+ 内部令牌写入 auth-server ----------
run "command -v java >/dev/null || yum install -y java-17-openjdk-headless"
run "mysql -e \"CREATE DATABASE IF NOT EXISTS pay_server DEFAULT CHARACTER SET utf8mb4\""
run "mysql -e \"CREATE USER IF NOT EXISTS 'payapp'@'127.0.0.1'\""
# 密码每次重置为脚本可控值（幂等），仅写入服务器端 .env，不回传本地
PAYAPP_DB_PW="${PAYAPP_DB_PW:-$(openssl rand -hex 16)}"
run "mysql -e \"ALTER USER 'payapp'@'127.0.0.1' IDENTIFIED BY '$PAYAPP_DB_PW'; FLUSH PRIVILEGES\""
run "mysql -e \"GRANT ALL PRIVILEGES ON pay_server.* TO 'payapp'@'127.0.0.1'; FLUSH PRIVILEGES\""
# auth-server .env 追加/更新内部令牌
run "grep -q '^INTERNAL_API_TOKEN=' /www/auth-server/.env 2>/dev/null && sed -i 's/^INTERNAL_API_TOKEN=.*/INTERNAL_API_TOKEN=$AUTH_INTERNAL_TOKEN/' /www/auth-server/.env || echo 'INTERNAL_API_TOKEN=$AUTH_INTERNAL_TOKEN' >> /www/auth-server/.env"

# ---------- 3. pay-server 目录 + .env + systemd ----------
run "mkdir -p /www/pay-server"
put "$JAR" /www/pay-server/pay-server.jar

TMP1="$(mktemp)"; TMP2="$(mktemp)"
cat > "$TMP1" <<ENV
DB_PASSWORD=$PAYAPP_DB_PW
AUTH_BASE_URL=http://127.0.0.1:8000
AUTH_INTERNAL_TOKEN=$AUTH_INTERNAL_TOKEN
STRIPE_SECRET_KEY=$STRIPE_SECRET_KEY
STRIPE_WEBHOOK_SECRET=${STRIPE_WEBHOOK_SECRET:-}
STRIPE_CURRENCY=${STRIPE_CURRENCY:-cny}
ENV
cat > "$TMP2" <<'UNIT'
[Unit]
Description=zhuzhu pay-server (Stripe Checkout)
After=network.target mysqld.service

[Service]
WorkingDirectory=/www/pay-server
EnvironmentFile=/www/pay-server/.env
ExecStart=/usr/bin/java -jar /www/pay-server/pay-server.jar
Restart=always
RestartSec=3
SuccessExitStatus=143

[Install]
WantedBy=multi-user.target
UNIT
put "$TMP1" /www/pay-server/.env
put "$TMP2" /etc/systemd/system/pay-server.service
rm -f "$TMP1" "$TMP2"

# ---------- 4. nginx /payapp/ 反代（幂等）----------
run "grep -q '/payapp/' /etc/nginx/conf.d/update-server.conf || sed -i '/location \/authapp\//i\\
    location /payapp/ {\\
        proxy_pass http://127.0.0.1:8090;\\
        proxy_set_header Host \$host;\\
        proxy_set_header X-Real-IP \$remote_addr;\\
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;\\
        proxy_set_header X-Forwarded-Proto \$scheme;\\
    }\\
' /etc/nginx/conf.d/update-server.conf"

# ---------- 5. 重启 + 健康检查 ----------
run "cd /www/auth-server && .venv/bin/python -c 'import app.main; print(\"AUTH IMPORT OK\")'"
run "systemctl restart auth-server && systemctl daemon-reload && systemctl enable --now pay-server"
sleep 5
run "systemctl is-active auth-server pay-server"
run "curl -s -o /dev/null -w 'pay health=%{http_code}\\n' http://127.0.0.1:8090/api/pay/order/x"
run "curl -s -o /dev/null -w 'nginx payapp=%{http_code}\\n' -H 'Authorization: Bearer x' https://chentian.dpdns.org/payapp/api/pay/order/x"
run "journalctl -u pay-server -n 10 --no-pager | tail -10"

echo
echo "================================================================"
echo "部署完成。Stripe Webhook URL（填入 Stripe 后台）："
echo "  https://chentian.dpdns.org/payapp/api/stripe/webhook"
echo "创建 webhook 后把签名密钥写入服务器并重启："
echo "  $SSH \"sed -i 's/^STRIPE_WEBHOOK_SECRET=.*/STRIPE_WEBHOOK_SECRET=whsec_xxx/' /www/pay-server/.env && systemctl restart pay-server\""
echo "内部发货令牌（已写入 auth-server 与 pay-server，勿泄露）："
echo "  $AUTH_INTERNAL_TOKEN"
echo "================================================================"
