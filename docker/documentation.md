# TriForce Docker Runtime

The production Docker runtime is intentionally split into independent Compose projects. There is no monolithic runtime Compose file anymore.

## Canonical configuration

- Source of truth: `config/triforce.env` (local, mode 0600, never committed).
- Runtime Compose projects: `docker/<stack>/docker-compose.yml`.
- Operator entrypoint: `scripts/docker/stack-control.sh`.
- Packaged installer blueprint: `docker/blueprint/` (kept separate on purpose).
- Secrets must not be committed or hard-coded in Compose/YAML. Use the canonical env file or service-specific secret files that are gitignored.

## Active stacks

| Stack | Main services | Persistence | Update policy |
| --- | --- | --- | --- |
| wordpress | Apache, WordPress FPM, MariaDB, Redis | bind-mounted webroot + named DB/Redis volumes | explicit version tags |
| flarum | Flarum, MariaDB | bind mounts + DB volume | Flarum digest pin, MariaDB minor pin |
| searxng | SearXNG | settings bind + named volume | digest pin |
| n8n | n8n | `data/n8n` | explicit n8n version |
| repository | nginx, optional apt-mirror job | repository bind mounts | nginx minor pin; mirror is profile-only |
| mailserver | docker-mailserver | mail/state/log bind mounts | explicit release tag |

## Operations

```bash
cd /home/zombie/workspace/triforce
scripts/docker/stack-control.sh config all
scripts/docker/stack-control.sh status all
scripts/docker/stack-control.sh start all
scripts/docker/stack-control.sh restart wordpress
scripts/docker/stack-control.sh logs wordpress
```

`restart` is idempotent `docker compose up -d --remove-orphans`; it applies changed configuration without unnecessarily tearing down networks and volumes.

## Safe update workflow

1. Back up the affected Compose/config files and persistent data.
2. Change image versions in `config/triforce.env` deliberately; do not switch production services to `latest` casually.
3. Run `scripts/docker/stack-control.sh config all`.
4. Pull only the intended stack image(s).
5. Apply one stack at a time with `restart <stack>`.
6. Wait for health checks and inspect logs before moving to the next stack.
7. Verify the public endpoint and dependent internal services.

## Stability rules

- No `docker compose down -v` in routine operations.
- Do not run apt-mirror as part of normal repository startup; use the `mirror` profile/job explicitly.
- Keep DB/Redis data in persistent volumes and application assets in deliberate bind mounts.
- Prefer health-gated dependencies where startup order matters.
- Keep Docker json-file logs bounded.
- Never store credentials in tracked files. Rotate any credential that was ever committed.
