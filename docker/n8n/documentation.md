# n8n production stack

n8n runs as an independent Compose project with persistent state in `data/n8n` and the image version pinned via `N8N_IMAGE` in `config/triforce.env`.

## Configuration

The canonical environment provides host/protocol/webhook URLs, SMTP credentials, MCP endpoint information, timezone, and optional proxy settings. The existing installation keeps its encryption key in the persistent n8n settings file; Compose does not override it. SMTP is defined directly in the main Compose file; the old local override layer is no longer required.

Key stability settings:

- The persistent encryption key must never be regenerated casually; keep the n8n data directory backed up.
- `N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=true`.
- JavaScript task runner uses the image default. Python Code-node execution requires a deliberately deployed external runner; the main image is not modified just to add Python.
- diagnostics disabled by default.
- reverse-proxy hop count explicitly configured.
- built-in user management is used; legacy Basic Auth environment variables are not treated as a security boundary.

## Operations

```bash
scripts/docker/stack-control.sh config n8n
scripts/docker/stack-control.sh restart n8n
scripts/docker/stack-control.sh logs n8n
```

Before any encryption-key migration, export/backup credentials and the full n8n data directory. Do not add a conflicting `N8N_ENCRYPTION_KEY` override to Compose.
