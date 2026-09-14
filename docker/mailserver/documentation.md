# AILinux mailserver stack

The mail stack uses docker-mailserver with persistent mail, state and log bind mounts plus read-only Let's Encrypt certificates.

## Production policy

- Image release is explicit via `MAILSERVER_IMAGE`.
- Host/domain/restart policy are variable driven from `config/triforce.env`.
- `SYS_PTRACE` is not granted.
- Spam/virus/greylisting components remain disabled unless there is an explicit operational reason to enable them; this reduces memory usage and moving parts.
- Mail data/state must be backed up before image upgrades.

## Operations

```bash
scripts/docker/stack-control.sh config mailserver
scripts/docker/stack-control.sh restart mailserver
scripts/docker/stack-control.sh status mailserver
scripts/docker/stack-control.sh logs mailserver
```

After changes verify SMTP submission, IMAPS, certificate validity, queue state and external deliverability.
