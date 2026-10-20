# Cloudflare credentials in TriForce (2026-10-20)

TriForce uses **distinct scoped API tokens**. Never substitute an R2 token,
Workers AI token, or Global API Key for a DNS token.

| Purpose | Canonical configuration | Consumer |
| --- | --- | --- |
| DNS-01 and authenticated DNS MCP tools | `CLOUDFLARE_DNS_API_TOKEN` | `scripts/cf-update-certs.sh`, `app/services/cloudflare_dns.py` |
| DNS zone ID | `CLOUDFLARE_ZONE_ID` | Same, must match `ailinux.me` |
| Cloudflare account | `CLOUDFLARE_ACCOUNT_ID` | Workers AI |
| Workers AI inference | `CLOUDFLARE_AI_WORKERS_API` | `app/services/provider_chat.py`, `app/routes/nova_frontend.py`, `app/routes/client_chat.py` |
| R2 S3 access ID | `CLOUDFLARE_R2_ACCESS_KEY_ID` | Future R2 S3 client |
| R2 S3 secret | `CLOUDFLARE_R2_SECRET_ACCESS_KEY` | Future R2 S3 client |
| R2 S3 endpoint | `CLOUDFLARE_R2_S3_ENDPOINT` | Future R2 S3 client |
| R2 account API token | `CLOUDFLARE_R2_API_TOKEN` | **Not** used by S3 client or DNS-01 |

Canonical secret location: `config/triforce.env`, mode `0600`, ignored by Git.

## Certificate maintenance

- One scheduler: `/etc/cron.d/ailinux-cert-renew`, at 03:17 and 15:17
  server time; `certbot.timer` and `ailinux-cert-renew.timer` disabled.
- The canonical renewal script performs a Cloudflare zone/permission
  preflight before touching `/etc/letsencrypt/cloudflare.ini`.
- Legacy entrypoints in `scripts/system/` and repository `certgen.sh`
  delegate to the canonical script. No Global API Key fallback, shell tracing,
  Snap installation, or duplicate private-key copies into the Git worktree.
- Safe read-only check:
  `sudo scripts/cf-update-certs.sh --check`
- Full ACME staging challenge:
  `sudo scripts/cf-update-certs.sh --dry-run`

## WordPress caching

`wp-cloudflare-page-cache` is third-party software and maintains **its own**
authentication configuration. Its current settings use legacy Global Key
mode. Do **not** insert the DNS-only token into the WordPress cache plugin:
cache purge is a separate permission. Migrate that plugin to a dedicated
scoped **Zone Cache Purge** API token via its supported token-auth setup,
backing up its configuration and testing purge behavior separately.
The TriForce DNS MCP tool no longer depends on that plugin or its key.

## R2

The `CLOUDFLARE_R2_*` names are AILinux-specific, not standard automatic
`boto3` environment variables. S3 clients must explicitly pass the access
key, secret, and endpoint. Cloudflare documents that the S3 secret is the
SHA-256 of the R2 API token value on issuance. Keep all values from the
same issued credential set. R2 is **not** verified merely by finding these
variables: test a signed S3 operation before enabling a production consumer.

When rotating credentials, change only the relevant scoped token, check
permissions without printing it, and rerun the associated integration tests.
Do not paste credentials into tickets, screenshots, logs or Git commits.

## Checks

- `.venv/bin/python -m pytest -q tests/test_cloudflare_dns_tools.py tests/test_provider_model_resolver.py`
- `systemctl is-active triforce cron`
- `curl -fsS https://api.ailinux.me/health`
- Backups for this migration: root-only under `/var/backups/ailinux-cert-renew/`
