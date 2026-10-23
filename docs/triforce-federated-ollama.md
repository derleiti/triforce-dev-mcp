# TriForce: private Ollama fleet

Architecture (2026-10-23):

| TriForce node | Ollama URL (hub-side) | Model | Runtime |
|---|---|---|---|
| hetzner / ailinux | http://localhost:11434 | openbmb/minicpm-v4.7:35b-a3b | CPU |
| backup | http://10.10.0.3:11434 | openbmb/minicpm-v4.7:1b | CPU |
| zombie-pc | http://10.10.0.2:11434 | gemma4:12b | AMD GPU (ROCm) |

All model IDs are exposed as `ollama/<full-ollama-tag>` in the common TriForce
catalog. Only models in `LOCAL_OLLAMA_PLACEMENT` appear, and only after their
assigned Ollama instance confirms the exact tag via `/api/tags`. A node failure
must not trigger an implicit switch to a different model/provider. Ollama Cloud
catalog behavior is unchanged.

### Transport and access

- Ollama itself stays on `127.0.0.1:11434` on the two remote workers.
- A systemd socket proxy listens **only** on `wg0`'s private address.
- The `zombie-pc` Firewalld zone `triforce-vpn` explicitly allows TCP 11434
  **only** from hub address `10.10.0.1/32`. No WAN listening socket is created.
- TriForce client endpoints require verified user JWT. Direct chat endpoints
  require an authenticated TriForce account or the server's existing internal key
  when requesting these pinned local models.
- The deliberately anonymous `/v1/nova/playground` endpoint does not allow
  selecting private fleet workers.
- Guest/free Cloud Ollama catalog remains independent; registered users and
  paid tiers can select the three local models when online. Token limits still
  apply to registered users.

### Installation and recovery

On `backup` and `zombie-pc`, after verifying WireGuard and localhost Ollama:
`scripts/install-triforce-ollama-wg.sh backup` or
`scripts/install-triforce-ollama-wg.sh zombie-pc`. The installed units are
recorded as `deploy/systemd/triforce-ollama-wg.{service,socket.template}`.
An earlier unit, if any, is backed up under
`/var/backups/ailinux/triforce-ollama-wg-20261023/`.

To disable only the VPN Ollama proxy on a worker (local Ollama stays running):

```bash
sudo systemctl disable --now triforce-ollama-wg.socket
sudo systemctl stop triforce-ollama-wg.service
```

On zombie-pc, also remove the Firewalld rule if rolling back this feature:

```bash
rule='rule family=ipv4 source address=10.10.0.1/32 port port=11434 protocol=tcp accept'
sudo firewall-cmd --zone=triforce-vpn --remove-rich-rule="$rule"
sudo firewall-cmd --permanent --zone=triforce-vpn --remove-rich-rule="$rule"
```

The TriForce code backup for this rollout is in
`/var/tmp/triforce-ollama-fleet-20261023/before`. Restore only relevant
changed files and restart TriForce if a code rollback is required.
Do not delete Ollama model weights as part of a routing rollback.

### Verification

From the hub:

```bash
curl -fsS http://10.10.0.3:11434/api/tags
curl -fsS http://10.10.0.2:11434/api/tags
curl -fsS http://127.0.0.1:11434/api/tags
```

The registry, authenticated client catalog, API routes, GPU offloading and
image/tool capabilities require separate smoke tests after each deploy.
Do not treat a successful `/api/tags` response as proof that inference works.
