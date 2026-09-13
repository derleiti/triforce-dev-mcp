#!/bin/bash
MAIN="/home/zombie/workspace/triforce/app/main.py"
if ! grep -q "nova_frontend import" "$MAIN"; then
  python3 - << 'EOF'
with open('/home/zombie/workspace/triforce/app/main.py', 'r') as f:
    txt = f.read()
if 'nova_frontend import' not in txt:
    txt = txt.replace(
        'from .routes.federation import router as federation_router',
        'from .routes.federation import router as federation_router\nfrom .routes.nova_frontend import router as nova_frontend_router'
    )
    with open('/home/zombie/workspace/triforce/app/main.py', 'w') as f:
        f.write(txt)
    print("[nova-guard] Import wiederhergestellt")
EOF
fi
