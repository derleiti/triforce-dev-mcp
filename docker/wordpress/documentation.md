# WordPress Stack Documentation

## Compose File
- `docker/wordpress/docker-compose.yml`

## Services
- `apache` (`httpd:2.4-alpine`)
- `wordpress_fpm` (`wordpress:6.8.1-php8.3-fpm-alpine`)
- `wordpress_db` (`mariadb:11`)
- `wordpress_redis` (`redis:alpine`)
- `wpcli` (`wordpress:cli`, profile `tools`)

## Purpose
- Main website runtime (Apache + PHP-FPM + MariaDB + Redis cache).

## Networks
- `${WP_NETWORK:-wordpress-network}` (local bridge)

## Ports
- HTTP: `${WP_HTTP_PORT:-80}`
- HTTPS: `${WP_HTTPS_PORT:-443}`

## Core Volumes
- `./html -> /var/www/html`
- `./apache/*` config mounts
- `./php/custom.ini`, `./php/www.conf`
- `./mysql/custom.cnf`
- `./redis/redis-optimized.conf`
- named volumes: `wp_db_data`, `wp_redis_data`

## Key Settings
- DB connection via `WORDPRESS_DB_*`
- Redis via `WORDPRESS_REDIS_*` and optional `WP_REDIS_PASSWORD`
- TLS certs from `/etc/letsencrypt` (read-only)

## Healthchecks
- Apache config test (`httpd -t`)
- FPM config validation (`php-fpm --test`)
- MariaDB ping
- Redis `PING` (with password when set)

## Typical Commands
```bash
cd /home/zombie/workspace/triforce/docker/wordpress
docker compose --env-file ../../.env up -d
docker compose ps
docker compose logs -f apache wordpress_fpm
```

## Operational Notes
- Keep `WORDPRESS_DB_PASSWORD` and `MYSQL_ROOT_PASSWORD` in `.env`, never in git.
- `wpcli` is for maintenance tasks, not a persistent runtime service.
