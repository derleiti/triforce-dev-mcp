# SearXNG Stack Documentation

## Compose File
- `docker/searxng/docker-compose.yml`

## Service
- `searxng` (`searxng/searxng:latest`)

## Purpose
- Privacy-focused metasearch endpoint for TriForce and frontend integrations.

## Network
- `${SEARXNG_NETWORK:-searxng-network}` (bridge)

## Port
- `${SEARXNG_PORT:-8089}:8080`

## Volumes
- `./settings.yml -> /etc/searxng/settings.yml` (read-only)
- named volume `${SEARXNG_DATA_VOLUME:-searxng-data} -> /etc/searxng`

## Key Settings
- `SEARXNG_BASE_URL`
- `SEARXNG_SECRET`
- `UWSGI_WORKERS`, `UWSGI_THREADS`
- `TZ`

## Security Hardening
- Drops all capabilities, only adds `CHOWN`, `SETGID`, `SETUID`.

## Healthcheck
- `wget -qO- http://localhost:8080/healthz`

## Typical Commands
```bash
cd /home/zombie/workspace/triforce/docker/searxng
docker compose --env-file ../../.env up -d
docker compose logs -f searxng
```
