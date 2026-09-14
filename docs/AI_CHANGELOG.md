# AI Change Log

## 2026-09-14 — Remote Docker compute sandbox + restart-safe pairing

- Scope: TriForce workspace pairing/compute bridge and AILinux Helper Android compute advertisement.
- Reason: allow an explicitly requested Internet-enabled Docker shell without exposing the TriForce host, while making an already paired share available at `~/workspace`.
- Security: dedicated egress network, host/private-network rejection, no Docker socket/host network/privileged mode, dropped capabilities, no-new-privileges, resource limits, per-session workspace mirror.
- Recovery: workspace write-back creates `.workspacebackup/<run>-compute-sandbox/backup.md`; source recovery snapshot is `.workspacebackup/20260914-045000-remote-compute-sandbox/`.
- Pairing: pre-lease pair tickets now survive backend restarts through hash-only Redis metadata; raw pairing codes are not persisted.
- Verification: focused pytest coverage, Helper source-contract tests, Android JDK17/SDK35 build, Docker public-Internet/host-isolation smoke test, then full TriForce regression suite.
