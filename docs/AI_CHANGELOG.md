# AI Change Log

## 2026-09-14 — Remote Docker compute sandbox + restart-safe pairing

- Scope: TriForce workspace pairing/compute bridge and AILinux Helper Android compute advertisement.
- Reason: allow an explicitly requested Internet-enabled Docker shell without exposing the TriForce host, while making an already paired share available at `~/workspace`.
- Security: dedicated egress network, host/private-network rejection, no Docker socket/host network/privileged mode, dropped capabilities, no-new-privileges, resource limits, per-session workspace mirror.
- Recovery: workspace write-back creates `.workspacebackup/<run>-compute-sandbox/backup.md`; source recovery snapshot is `.workspacebackup/20260914-045000-remote-compute-sandbox/`.
- Pairing: pre-lease pair tickets now survive backend restarts through hash-only Redis metadata; raw pairing codes are not persisted.
- Verification: focused pytest coverage, Helper source-contract tests, Android JDK17/SDK35 build, Docker public-Internet/host-isolation smoke test, then full TriForce regression suite.

## 2026-09-14 — MCP workspace reconnect + Helper 2.90.29 hardening

- Scope: `/v1/mcp` server/web Helper, workspace pairing/resume, MCP agent instructions, and AILinux Helper Android lifecycle.
- Root causes: reconnect-by-pair-code did not return the persisted resume credential; Android could keep a transport-looking socket while no inbound frames arrived; capability changes from Accessibility readiness were not re-advertised until a later reconnect.
- Server fixes: reconnect responses now return the existing resume credential; regression coverage verifies it; `HTTPException` is imported for Helper download routes; MCP Python modules no longer carry accidental executable mode bits.
- MCP client guidance: initialize instructions now include a compact workspace operating guide covering status/transport/executor distinction, short-lived pair codes vs durable resume credentials, Android Accessibility capability gating, semantic observe/invoke/observe control, and secret handling.
- Web Helper: browser/PWA executor/cache assets advanced to 2.90.29 with matching regression expectations.
- Verification: 101 focused TriForce MCP/workspace tests passed; Python compile and `git diff --check` passed; code audit reports no error/critical bug/security findings in the audited MCP routes after fixes.
- Recovery: `/home/zombie/workspace/.workspacebackup/retry-20260914-183651/`.
