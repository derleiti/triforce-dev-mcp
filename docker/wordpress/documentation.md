# WordPress production stack

## Architecture

Apache terminates HTTP/TLS and proxies PHP to WordPress PHP-FPM. MariaDB stores durable application data and Redis is used as an object cache. The webroot is bind-mounted so the deployed site remains explicit and inspectable.

## Canonical settings

Runtime values come from `../../config/triforce.env`. Important keys include `WP_FPM_IMAGE`, `WP_DB_IMAGE`, `WP_REDIS_IMAGE`, `WORDPRESS_DB_*`, `WP_REDIS_PASSWORD`, `WP_DISALLOW_FILE_EDIT`, `WP_FORCE_SSL_ADMIN`, and `WP_ENVIRONMENT_TYPE`.

The persisted `wp-config.php` uses the official Docker `getenv_docker()` pattern. Database credentials and AILinux production flags therefore come from environment variables instead of duplicated literal configuration.

## Performance policy

- One PHP tuning file: `php/custom.ini`.
- One OPCache profile: `php/opcache-boost.ini`.
- One FPM pool: `php/www.conf`.
- One MariaDB tuning file: `mysql/custom.cnf`.
- Redis cache: `redis/redis-optimized.conf` with bounded memory and RDB persistence.
- PHP JIT is disabled: WordPress benefits more from predictable OPCache behavior than JIT complexity.

Historical `www-optimized.conf`, `zz-ailinux-opcache.ini`, and the unused nested MariaDB performance file were retired to prevent conflicting effective settings.

## Operations

```bash
cd /home/zombie/workspace/triforce
scripts/docker/stack-control.sh config wordpress
scripts/docker/stack-control.sh restart wordpress
scripts/docker/stack-control.sh status wordpress
scripts/docker/stack-control.sh logs wordpress
```

For WP-CLI use the `tools` profile only when needed. Never delete `wp_db_data` during routine maintenance.
