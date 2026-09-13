#!/bin/bash
set -e

REMOTE="/home/zombie/workspace/triforce/app/routes/mcp_remote.py"

# Backup
cp "$REMOTE" "${REMOTE}.bak_patch_$(date +%Y%m%d_%H%M%S)"
echo "Backup OK"

# Fix: public_public_router → public_router
sed -i 's/public_public_router/public_router/g' "$REMOTE"

# Verify
COUNT=$(grep -c "public_router" "$REMOTE")
DOUBLE=$(grep -c "public_public_router" "$REMOTE" 2>/dev/null || echo 0)
echo "public_router Vorkommen: $COUNT"
echo "public_public_router (kaputt) verbleibend: $DOUBLE"

# Syntax check
python3 -c "import ast; ast.parse(open('$REMOTE').read()); print('Syntax OK')"

# Restart
sudo systemctl restart triforce
sleep 5
sudo systemctl is-active triforce
