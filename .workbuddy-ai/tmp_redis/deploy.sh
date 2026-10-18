#!/bin/bash
# Redis cache-node deployment for Debian 12 (BT Panel box)
# Idempotent. Run as root. Password supplied via $REDIS_PASS.
set -euo pipefail

PASS="${REDIS_PASS:?REDIS_PASS not set}"
TS=$(date +%Y%m%d-%H%M%S)

echo "=============== [1/8] Backup ==============="
cp -a /etc/redis/redis.conf "/etc/redis/redis.conf.bak.$TS"
echo "backup -> /etc/redis/redis.conf.bak.$TS"

echo "=============== [2/8] Redis config ==============="
cat > /etc/redis/redis.conf <<EOF
#############################################################
# Redis cache node - managed config
# deployed: $TS
# role: shared cache for website + client API
#############################################################

################## NETWORK ##################
bind 0.0.0.0
protected-mode yes
port 6379
tcp-backlog 8192
timeout 0
tcp-keepalive 300
unixsocket /run/redis/redis-server.sock
unixsocketperm 770

################## GENERAL ##################
daemonize no
supervised systemd
pidfile /run/redis/redis-server.pid
loglevel notice
logfile /var/log/redis/redis-server.log
databases 16
always-show-logo no
set-proc-title yes
proc-title-template "{title} {listen-addr} {server-mode}"

################## SNAPSHOTTING ##################
# Cache workload: light RDB only, no AOF (max write throughput).
save 900 1
save 300 10
stop-writes-on-bgsave-error no
rdbcompression yes
rdbchecksum yes
dbfilename dump.rdb
dir /var/lib/redis
appendonly no

################## MEMORY ##################
# Box has 1.9G RAM shared with MySQL/PHP/panel -> hard cap here.
maxmemory 384mb
maxmemory-policy allkeys-lru
maxmemory-samples 5
lazyfree-lazy-eviction yes
lazyfree-lazy-expire yes
lazyfree-lazy-server-del yes
lazyfree-lazy-user-del yes
lazyfree-lazy-user-flush yes

################## LATENCY / THROUGHPUT ##################
# 2 vCPU box: keep the main thread single (io-threads>1 needs 4+ cores).
hz 10
dynamic-hz yes
latency-monitor-threshold 100

################## CLIENTS ##################
maxclients 10000

################## SECURITY ##################
requirepass $PASS
# Data-destruction / debug commands disabled on a shared cache node.
rename-command FLUSHALL ""
rename-command FLUSHDB ""
rename-command DEBUG ""

################## LOGGING ##################
slowlog-log-slower-than 10000
slowlog-max-len 256

################## NOTIFICATIONS ##################
notify-keyspace-events ""

################## CLIENTS 2 ##################
client-output-buffer-limit normal 0 0 0
client-output-buffer-limit replica 256mb 64mb 60
client-output-buffer-limit pubsub 32mb 8mb 60
EOF
chown redis:redis /etc/redis/redis.conf
chmod 640 /etc/redis/redis.conf
echo "config written"

echo "=============== [3/8] systemd drop-in (fix ExecPaths 127) ==============="
mkdir -p /etc/systemd/system/redis-server.service.d
cat > /etc/systemd/system/redis-server.service.d/override.conf <<'EOF'
[Service]
# Debian ships /usr/bin/redis-server as a symlink to redis-check-rdb.
# The packaged unit whitelists only /usr/bin/redis-server via NoExecPaths=/,
# so the resolved target is refused and the service dies with exit 127.
ExecPaths=/usr/bin/redis-server /usr/bin/redis-check-rdb /usr/bin/redis-check-aof /usr/lib /lib

LimitNOFILE=65535
OOMScoreAdjust=-500
Restart=always
RestartSec=2
TimeoutStopSec=30
EOF
echo "drop-in written"

echo "=============== [4/8] Kernel tuning ==============="
cat > /etc/sysctl.d/99-redis.conf <<'EOF'
# Redis: allow background-save fork with overcommitted memory
vm.overcommit_memory = 1
# Redis: keep it in RAM, avoid swapping hot cache
vm.swappiness = 1
# Accept bursts of new connections under high concurrency
net.core.somaxconn = 8192
net.ipv4.tcp_max_syn_backlog = 8192
fs.file-max = 200000
EOF
sysctl --system >/dev/null 2>&1 || true
echo "vm.overcommit_memory=$(cat /proc/sys/vm/overcommit_memory)  somaxconn=$(cat /proc/sys/net/core/somaxconn)"

echo "=============== [5/8] Disable THP ==============="
cat > /etc/systemd/system/disable-thp.service <<'EOF'
[Unit]
Description=Disable Transparent Huge Pages (Redis latency)
After=sysinit.target local-fs.target
[Service]
Type=oneshot
ExecStart=/bin/sh -c 'echo never > /sys/kernel/mm/transparent_hugepage/enabled'
RemainAfterExit=yes
[Install]
WantedBy=basic.target
EOF
echo never > /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || true
systemctl daemon-reload
systemctl enable disable-thp.service >/dev/null 2>&1 || true
echo "THP: $(cat /sys/kernel/mm/transparent_hugepage/enabled)"

echo "=============== [6/8] Swap (fork safety) ==============="
if swapon --show 2>/dev/null | grep -q swapfile; then
  echo "swap already active"
else
  if [ ! -f /swapfile ]; then
    fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
  fi
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "2G swap enabled"
fi
free -h | head -3

echo "=============== [7/8] Firewall ==============="
ufw allow 6379/tcp >/dev/null 2>&1 || true
ufw reload >/dev/null 2>&1 || true
ufw status | grep -E '6379|Status' || true

echo "=============== [8/8] Start service ==============="
systemctl daemon-reload
systemctl enable redis-server >/dev/null 2>&1
systemctl restart redis-server
sleep 2
systemctl is-active redis-server
echo "--- status ---"
systemctl status redis-server --no-pager | head -8
echo "--- ping ---"
redis-cli -a "$PASS" --no-auth-warning ping
echo "--- version ---"
redis-cli -a "$PASS" --no-auth-warning info server | grep -E 'redis_version|tcp_port|config_file|uptime_in_seconds'
echo "--- memory ---"
redis-cli -a "$PASS" --no-auth-warning info memory | grep -E 'maxmemory_human|maxmemory_policy|used_memory_human'
echo "DEPLOY_OK"
