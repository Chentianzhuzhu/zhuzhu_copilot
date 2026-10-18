#!/bin/bash
# ============================================================
# offsite_retention.sh - run on the BACKUP node (RainYun, root)
# Verify newest backup set, prune >7 days, warn if stale.
# ============================================================
B=/home/bkpush/backups
LOG=/var/log/offsite_retention.log
KEEP_DAYS=7
STALE_HOURS=30

{
echo "=== offsite retention $(date '+%F %T') ==="

LATEST=$(ls -1d $B/20* 2>/dev/null | sort | tail -1)

if [ -n "$LATEST" ] && [ -f "$LATEST/SHA256SUMS" ]; then
  if (cd "$LATEST" && sha256sum -c SHA256SUMS >/dev/null 2>&1); then
    echo "integrity: OK ($LATEST)"
  else
    echo "integrity: FAILED for $LATEST"
  fi
else
  echo "integrity: no backup set found in $B"
fi

find "$B" -maxdepth 1 -type d -name '20*' -mtime +$KEEP_DAYS -print -exec rm -rf {} + 2>/dev/null

if [ -n "$LATEST" ]; then
  H=$(( ( $(date +%s) - $(stat -c %Y "$LATEST") ) / 3600 ))
  echo "latest=$LATEST age_hours=$H"
  if [ "$H" -gt $STALE_HOURS ]; then
    echo "stale: WARN backup older than ${STALE_HOURS}h - check app server cron"
  fi
fi

echo "disk_free=$(df -h / | awk 'NR==2{print $4}')"
echo "sets_kept=$(ls -1d $B/20* 2>/dev/null | wc -l)"
} >> "$LOG" 2>&1
