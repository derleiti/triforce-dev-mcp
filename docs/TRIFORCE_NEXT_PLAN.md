# TriForce Next – execution plan

Started 2026-11-24. Existing production is the rollback baseline. Work on
isolated branches; never overwrite unrelated local changes. No paid cloud
resources or automatic destructive migrations.

| Gate | Work | Exit evidence |
|---|---|---|
| 1 | PR #181 review, notification durability, mirror manifest validation | Review addressed, isolated full tests, remote CI |
| 2 | Helper packaging and permissions; preserve Android work | New Linux version, package checks, normal-user install/start, client tests |
| 3 | MCP and released client matrix | All existing HTTP transports, auth, helper pairing and AICoder contracts; native tests where available |
| 4 | Staged backend integration | Merge only after checks; preserve mirror changes; backups, one node at a time, health and rollback |
| 5 | Client release and distribution | Signed/verified artifacts, consistent manifest, APT signed indexes and downloads; product pages only after verification |
| 6 | Security debt and dependency upgrades | Inventory actual production vs dev dependencies, prioritize critical, test each compatible update |
| 7 | Provider auth, MCP/security and performance | Reproduce individual faults, fix with regression tests; preserve client wire compatibility |
| 8 | Brev / compute | Read-only inventory and optional fallback; no paid provisioning without approval |

Use docs/TRIFORCE_NEXT_COMPATIBILITY.md as the release acceptance contract.
Record blockers explicitly: do not mark an untested platform as supported by
the new release merely because its old artifact exists.
