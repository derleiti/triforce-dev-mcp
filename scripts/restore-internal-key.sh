#!/bin/bash
# Auto-restore INTERNAL_API_KEY in triforce.env if missing.
# Band-Aid until the rollback-mechanism is identified.
ENV_FILE="/home/zombie/workspace/triforce/config/triforce.env"
KEY="INTERNAL_API_KEY=7dffc1818b9ee6d35b075b8922d6eecb3c9a5b9bfdf94df6"

if [ -f "$ENV_FILE" ] && ! grep -q "^INTERNAL_API_KEY=" "$ENV_FILE"; then
    echo "" >> "$ENV_FILE"
    echo "# Auto-restored by restore-internal-key.sh at $(date)" >> "$ENV_FILE"
    echo "$KEY" >> "$ENV_FILE"
    logger -t triforce-env "INTERNAL_API_KEY was missing, re-inserted"
    # Restart so backend picks it up
    systemctl restart triforce
fi
