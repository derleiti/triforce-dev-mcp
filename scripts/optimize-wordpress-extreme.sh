#!/usr/bin/env bash
# ============================================================================
# 🔥 EXTREME WORDPRESS OPTIMIZATION SCRIPT 🔥
# AILinux DevOps - Performance Tuning
# Ausführen mit: sudo bash scripts/optimize-wordpress-extreme.sh
# ============================================================================

set -Eeuo pipefail
WP_DIR="${AILINUX_WP_DIR:-/home/zombie/triforce/docker/wordpress}"
[[ -d "$WP_DIR" ]] || { echo "WordPress stack not found: $WP_DIR" >&2; exit 1; }
cd "$WP_DIR"

echo "🔥 EXTREME WORDPRESS OPTIMIZATION 🔥"
echo "====================================="
echo ""

# ============================================================================
# 1. PHP-FPM OPTIMIZATION
# ============================================================================
echo "📦 [1/5] Optimizing PHP-FPM..."

cat > php/www-optimized.conf << 'EOF'
[www]
user = www-data
group = www-data
listen = 9000
listen.owner = www-data
listen.group = www-data
listen.mode = 0660

; Process Management - OPTIMIZED FOR 12 CORES, 32GB RAM
pm = dynamic
pm.max_children = 75
pm.start_servers = 15
pm.min_spare_servers = 10
pm.max_spare_servers = 30
pm.max_requests = 1000
pm.process_idle_timeout = 30s

; Status
pm.status_path = /status
ping.path = /ping
ping.response = pong

; Logging
slowlog = /dev/stderr
request_slowlog_timeout = 5s
request_terminate_timeout = 300s

; Security
security.limit_extensions = .php
EOF
echo "   ✅ PHP-FPM config optimiert (75 workers, 1000 requests)"

# ============================================================================
# 2. PHP OPCache BOOST
# ============================================================================
echo "📦 [2/5] Boosting OPCache..."

cat > php/opcache-boost.ini << 'EOF'
; OPCache Extreme Boost
opcache.enable=1
opcache.enable_cli=1
opcache.memory_consumption=512
opcache.interned_strings_buffer=64
opcache.max_accelerated_files=50000
opcache.revalidate_freq=120
opcache.validate_timestamps=1
opcache.fast_shutdown=1
opcache.jit=1255
opcache.jit_buffer_size=256M
opcache.save_comments=1
opcache.huge_code_pages=1
EOF
echo "   ✅ OPCache auf 512MB + JIT 256MB"

# ============================================================================
# 3. MYSQL/MARIADB TUNING
# ============================================================================
echo "📦 [3/5] MySQL InnoDB Tuning..."

mkdir -p mysql/conf.d
cat > mysql/conf.d/performance.cnf << 'EOF'
[mysqld]
# InnoDB Buffer Pool - 2GB für 32GB RAM System
innodb_buffer_pool_size = 2G
innodb_buffer_pool_instances = 4
innodb_log_file_size = 512M
innodb_log_buffer_size = 64M
innodb_flush_method = O_DIRECT
innodb_flush_log_at_trx_commit = 2
innodb_io_capacity = 2000
innodb_io_capacity_max = 4000

# Query Cache (für MariaDB)
query_cache_type = 1
query_cache_size = 128M
query_cache_limit = 4M

# Connections
max_connections = 200
thread_cache_size = 50
table_open_cache = 4000
table_definition_cache = 2000

# Temp Tables
tmp_table_size = 256M
max_heap_table_size = 256M

# Logging
slow_query_log = 1
slow_query_log_file = /var/log/mysql/slow.log
long_query_time = 2

# Performance Schema
performance_schema = ON
EOF
echo "   ✅ InnoDB Buffer Pool 2GB, Query Cache 128MB"

# ============================================================================
# 4. REDIS OPTIMIZATION
# ============================================================================
echo "📦 [4/5] Redis Memory Optimization..."

cat > redis/redis-optimized.conf << 'EOF'
# Redis Optimized Config
maxmemory 1gb
maxmemory-policy allkeys-lru
activedefrag yes
lazyfree-lazy-eviction yes
lazyfree-lazy-expire yes
lazyfree-lazy-server-del yes

# Persistence (RDB only, no AOF for speed)
save 900 1
save 300 10
save 60 10000
appendonly no

# Network
tcp-keepalive 300
timeout 0

# Memory
hash-max-ziplist-entries 512
hash-max-ziplist-value 64
list-max-ziplist-size -2
set-max-intset-entries 512
EOF
echo "   ✅ Redis 1GB mit LRU Eviction"

# ============================================================================
# 5. APACHE PERFORMANCE
# ============================================================================
echo "📦 [5/5] Apache Performance..."

# Check if mod_deflate and mod_expires configs exist
if [ -f apache/extra/httpd-performance.conf ]; then
    echo "   ⚠️ Apache config existiert bereits"
else
    cat > apache/extra/httpd-performance.conf << 'EOF'
# Apache Performance Tuning
<IfModule mod_deflate.c>
    SetOutputFilter DEFLATE
    SetEnvIfNoCase Request_URI \.(?:gif|jpe?g|png|webp|ico|woff2?)$ no-gzip
    DeflateCompressionLevel 6
</IfModule>

<IfModule mod_expires.c>
    ExpiresActive On
    ExpiresByType image/jpeg "access plus 1 year"
    ExpiresByType image/png "access plus 1 year"
    ExpiresByType image/webp "access plus 1 year"
    ExpiresByType image/svg+xml "access plus 1 year"
    ExpiresByType text/css "access plus 1 month"
    ExpiresByType application/javascript "access plus 1 month"
    ExpiresByType font/woff2 "access plus 1 year"
</IfModule>

<IfModule mod_headers.c>
    Header set Cache-Control "public, max-age=31536000" env=!no-cache
</IfModule>

# Keep-Alive
KeepAlive On
MaxKeepAliveRequests 500
KeepAliveTimeout 5
EOF
    echo "   ✅ Apache Compression + Caching"
fi

# ============================================================================
# SUMMARY
# ============================================================================
echo ""
echo "============================================"
echo "✅ OPTIMIZATION FILES CREATED!"
echo "============================================"
echo ""
echo "Änderungen:"
echo "  • PHP-FPM: 75 workers, 1000 max_requests"
echo "  • OPCache: 512MB + JIT 256MB"
echo "  • MySQL: 2GB Buffer Pool, Query Cache 128MB"
echo "  • Redis: 1GB maxmemory mit LRU"
echo "  • Apache: Compression + Long-term Caching"
echo ""
echo "🔧 Nächste Schritte:"
echo "1. docker-compose.yml anpassen (siehe unten)"
echo "2. docker compose down && docker compose up -d"
echo ""
echo "Beispiel docker-compose.yml Änderungen:"
echo "  wordpress_fpm:"
echo "    volumes:"
echo "      - ./php/www-optimized.conf:/usr/local/etc/php-fpm.d/zz-www.conf"
echo "      - ./php/opcache-boost.ini:/usr/local/etc/php/conf.d/opcache.ini"
echo ""
echo "  wordpress_db:"
echo "    volumes:"
echo "      - ./mysql/conf.d:/etc/mysql/conf.d"
echo ""
echo "  wordpress_redis:"
echo "    command: redis-server /usr/local/etc/redis/redis.conf"
echo "    volumes:"
echo "      - ./redis/redis-optimized.conf:/usr/local/etc/redis/redis.conf"
echo ""
