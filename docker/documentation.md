# Docker Stack Documentation

This folder contains the multi-stack Docker setup for TriForce.

## Compose File
- `docker/docker-compose.yml`

## Included Services (profile-based)
- `wordpress` + `db` (`profile: wordpress`)
- `searxng` (`profile: searxng`)
- `repo` (`profile: repo`)
- `mailserver` (`profile: mailserver`)
- `redis` (`profile: redis`)

## Networks
- `triforce-net` (bridge)

## Default Ports (host:container)
- WordPress: `8080:80`
- SearXNG: `8888:8080`
- Repo (nginx): `8081:80`
- Mail: `25/587/465/143/993`
- Redis: `6379:6379`

## Typical Commands
```bash
cd /home/zombie/workspace/triforce/docker
docker compose --profile wordpress --profile searxng --profile repo up -d
docker compose ps
docker compose logs -f
```

## Notes
- Runtime values come from `config/triforce.env` and/or `.env`.
- Stack-specific details are documented in each subfolder `documentation.md`.
