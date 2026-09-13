#!/bin/bash
# BUG-001 FIX: time_up.log truncaten + logrotate einrichten
# Ausführen mit: sudo bash /home/zombie/workspace/triforce/scripts/fix_time_up_log.sh

set -e

LOG_FILE="/home/zombie/workspace/triforce/time_up.log"
LOGROTATE_CONF="/etc/logrotate.d/triforce-time-up"

echo "[1/3] Leere time_up.log (aktuell: $(du -sh "$LOG_FILE" | cut -f1))"
truncate -s 0 "$LOG_FILE"
echo "      → Erledigt. Neue Größe: $(du -sh "$LOG_FILE" | cut -f1)"

echo "[2/3] Setze Berechtigungen auf zombie:zombie"
chown zombie:zombie "$LOG_FILE"
echo "      → Erledigt."

echo "[3/3] Erstelle logrotate-Config: $LOGROTATE_CONF"
cat > "$LOGROTATE_CONF" << 'EOF'
/home/zombie/workspace/triforce/time_up.log {
    daily
    rotate 3
    compress
    delaycompress
    missingok
    notifempty
    size 10M
    create 0644 zombie zombie
    postrotate
        systemctl restart triforce 2>/dev/null || true
    endscript
}
EOF
echo "      → logrotate config geschrieben."

echo ""
echo "✓ BUG-001 vollständig behoben."
echo "  logrotate test: logrotate --debug $LOGROTATE_CONF"
