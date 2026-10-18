#!/bin/bash
# ============================================================
# offsite_backup.sh - push nightly backup to RainYun node
# The remote key is rrsync-restricted: it can ONLY write into
# /home/bkpush/backups and can NEVER run commands there.
# ============================================================
set -euo pipefail

DATE=$(date +%F)
STAGE=/var/backups/offsite/$DATE
REMOTE_HOST=122.228.216.138
REMOTE_DIR="$DATE"
KEY=/root/.ssh/bkpush_ed25519
MYCNF=/root/.my_bak.cnf
LOG=/var/log/offsite_backup.log
KEEP_LOCAL_DAYS=2

log() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }

mkdir -p "$STAGE"
cd "$STAGE"
log "=== offsite backup start ($DATE) ==="

# 1) MySQL dump: all business databases, InnoDB-consistent snapshot
DBS="mysql doudizhu lost_found zhuzhu_copilot_auth winapp_update"
mysqldump --defaults-file=$MYCNF --single-transaction --routines \
  --triggers --events --databases $DBS 2>>"$LOG" | gzip -6 > mysql_$DATE.sql.gz
if ! zgrep -q 'Dump completed' mysql_$DATE.sql.gz; then
  log "FATAL: mysqldump output missing completion marker"
  exit 1
fi
log "mysql dump: $(du -h mysql_$DATE.sql.gz | cut -f1)"

# 2) File snapshot: app code (no venv/pycache) + system nginx config
tar czf files_$DATE.tar.gz --ignore-failed-read \
  --exclude='www/auth-server/.venv' \
  --exclude='__pycache__' --exclude='*.pyc' \
  -C / www/auth-server etc/nginx
log "files tar: $(du -h files_$DATE.tar.gz | cut -f1)"

# 3) Manifest + checksums
{
  echo "host=$(hostname)"
  echo "date=$DATE"
  echo "databases=$DBS"
  echo "nginx=$(nginx -v 2>&1 || true)"
  echo "kernel=$(uname -r)"
  echo "authserver_files=$(du -sh --exclude='.venv' /www/auth-server | cut -f1)"
  crontab -l 2>/dev/null | sed 's/^/crontab: /' || true
} > manifest.txt
sha256sum mysql_$DATE.sql.gz files_$DATE.tar.gz manifest.txt > SHA256SUMS

# 4) Verify locally before push
sha256sum -c SHA256SUMS >>"$LOG" 2>&1

# 5) Push to offsite node
rsync -az --timeout=300 \
  -e "ssh -i $KEY -o StrictHostKeyChecking=yes -o PasswordAuthentication=no -o ConnectTimeout=15" \
  "$STAGE/" "bkpush@$REMOTE_HOST:$REMOTE_DIR/"
log "rsync done -> $REMOTE_HOST:$REMOTE_DIR"

# 6) Trim local staging copies
find /var/backups/offsite -maxdepth 1 -type d -name '20*' -mtime +$KEEP_LOCAL_DAYS \
  -exec rm -rf {} + 2>/dev/null || true

log "=== offsite backup OK ==="
