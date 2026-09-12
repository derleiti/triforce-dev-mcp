#!/usr/bin/env bash
# ============================================================================
# 🚀 WORDPRESS OPTIMIZED DEPLOYMENT 🚀
# Führt alle Optimierungen aus und startet Container neu
# Ausführen mit: sudo bash scripts/deploy-wordpress-optimized.sh
# ============================================================================

set -e
WP_DIR="/home/zombie/triforce/docker/wordpress"
cd "$WP_DIR"

echo "🚀 WORDPRESS OPTIMIZED DEPLOYMENT"
echo "=================================="

# Step 1: Create optimization configs
echo ""
echo "📦 [1/4] Creating optimization configs..."
bash /home/zombie/triforce/scripts/optimize-wordpress-extreme.sh

# Step 2: Backup current docker-compose.yml
echo ""
echo "📦 [2/4] Backing up docker-compose.yml..."
cp docker-compose.yml "docker-compose.yml.bak.$(date +%Y%m%d_%H%M%S)"

# Step 3: Create redis directory if missing
mkdir -p redis

# Step 4: Update docker-compose.yml with optimized mounts
echo ""
echo "📦 [3/4] Patching docker-compose.yml..."

# Check if already patched
if grep -q "www-optimized.conf" docker-compose.yml; then
    echo "   ⚠️ Already patched, skipping..."
else
    # Add PHP-FPM optimized config mount
    sed -i '/- .\/php\/custom.ini:\/usr\/local\/etc\/php\/conf.d\/custom.ini/a\      - ./php/www-optimized.conf:/usr/local/etc/php-fpm.d/zz-www.conf\n      - ./php/opcache-boost.ini:/usr/local/etc/php/conf.d/zz-opcache.ini' docker-compose.yml
    echo "   ✅ PHP-FPM mounts added"
fi

# Check if MySQL conf.d mounted
if grep -q "mysql/conf.d" docker-compose.yml; then
    echo "   ⚠️ MySQL conf.d already mounted"
else
    # Find wordpress_db service and add volume
    # This is tricky, manual intervention may be needed
    echo "   ⚠️ MySQL conf.d mount needs manual addition"
fi

# Step 5: Restart containers
echo ""
echo "📦 [4/4] Restarting WordPress containers..."
docker compose down
sleep 2
docker compose up -d

# Wait for health
echo ""
echo "⏳ Waiting for containers to become healthy..."
sleep 10

# Status check
echo ""
echo "============================================"
echo "📊 CONTAINER STATUS"
echo "============================================"
docker compose ps

echo ""
echo "============================================"
echo "✅ DEPLOYMENT COMPLETE!"
echo "============================================"
echo ""
echo "Performance Tests:"
echo "  curl -w '%{time_total}s' -o /dev/null -s https://ailinux.me/"
echo ""
echo "Redis Stats:"
echo "  docker exec wordpress_redis redis-cli info stats | grep keyspace"
echo ""
echo "OPCache Status:"
echo "  docker exec wordpress_fpm php -i | grep opcache.memory"
echo ""
