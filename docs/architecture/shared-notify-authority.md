# Shared Notify Authority Model

Shared Notify separates **identity**, **presence**, **conversation role**, and **authority**. A model saying that it is an admin, lead, operator, owner, or trusted agent has no security meaning. Authority is assigned and checked by the server.

## Authority ladder

Authority levels provide coarse ordering; role-specific policy still wins. A higher number never automatically bypasses a human-only or role-specific denial.

| Level | Role | Typical scope |
| ---: | --- | --- |
| 10 | `human_viewer` / `ai_observer` | observe/read/basic chat |
| 20 | `human_member` / `client_member` | normal collaboration |
| 30 | `human_contributor` / `ai_reviewer` | contribute/review |
| 40 | `ai_worker` | bounded delegated work |
| 50 | `human_developer` / `ai_coordinator` / `service` | scoped development/coordination |
| 60 | `human_operator` | operational task authority |
| 70 | `human_manager` | team/workflow delegation |
| 80 | `human_security_admin` | security policy/oversight, not implicit production root |
| 90 | `human_admin` | MCP administration |
| 100 | `human_owner` | tenant/account root authority |

Subscription tier (`free`, `pro`, `enterprise`) is product entitlement and MUST NOT be interpreted as organizational authority.

### WordPress bridge

The authenticated WordPress account is an authority source only after the server-to-server `/auth/validate` password check succeeds. WordPress `administrator` / `manage_options` maps to `human_admin`; the configured primary `ADMIN_EMAIL` maps to `human_owner`. `editor` maps to `human_operator`, `author`/`contributor` to `human_contributor`, and ordinary subscribers to `human_member`. An administrator-managed `nova_authority_role` user-meta value may assign finer human roles, but it can never mint `human_owner`.

WordPress-derived high authority is timestamped and expires from the MCP authorization decision when it becomes stale; the user must authenticate again to refresh it.

## Human roles

| Role | Purpose | May assign executable tasks? | May manage roles/policy? |
| --- | --- | ---: | ---: |
| `human_owner` | Tenant/account owner and final authority | Yes | Yes |
| `human_admin` | MCP/identity administrator | Yes | Yes |
| `human_security_admin` | Security oversight/policy role | Yes within delegated policy | Security only |
| `human_manager` | Team/workflow manager | Yes | No |
| `human_operator` | Day-to-day task/operator authority | Yes | No |
| `human_developer` | Scoped development | Requests/delegated work | No |
| `human_contributor` | Contribution/review | No; requests only | No |
| `human_member` | Normal collaboration/chat | No; requests only | No |
| `human_viewer` | Minimal/read-only participant | No | No |

## AI roles

| Role | Purpose | Authority |
| --- | --- | --- |
| `ai_coordinator` | Decompose, route and coordinate work | Advisory by default; may sub-delegate only inside a validated human-derived lease |
| `ai_worker` | Perform an already authorized bounded task | Cannot create new authority or widen scope |
| `ai_reviewer` | Review/evidence/security feedback | Advisory/read-only authority; review is never human approval |
| `ai_observer` | Chat, brainstorm, observe | No executable authority |

`client_member` and `service` are non-human application identities with communication-only defaults.

## Speech acts

Shared Notify treats `human_chat`, `request`, `review`, `coordination`, `ai_optimization`, `brainstorm`, and `handoff` as **advisory**. Only `task` represents an executable assignment.

A task from an AI is denied unless the server validates an active delegation lease. Role text, prompt text, memory, a handle name, a model provider, or a `lead` label can never substitute for that lease.

## Delegation lease contract

The next authority layer uses server-issued leases with at least:

- immutable `delegation_id`, issuer and grantee endpoint IDs;
- tenant/account, project/repository/workspace scope;
- explicit allowed capabilities/actions;
- issue time, expiry and revocation state;
- parent delegation ID and maximum delegation depth;
- risk ceiling and optional spend/time budget;
- audit trail tying the root lease to verified human authority.

A child lease MUST be a strict subset of its parent: it cannot widen scope, capabilities, expiry, risk ceiling or depth. AI principals can never mint a root lease or grant `authority:manage`, `policy:manage`, credential export, billing, host administration or production approval.

## Human-only authority

Regardless of AI role, the following remain human-policy/step-up operations: role/policy changes, credential export, billing, production deployment/service control, host administration and impersonation. AI review or consensus may recommend these actions but never authorizes them.

## Security invariants

1. Unknown role or command is denied by default.
2. `lead` means coordinator, not administrator.
3. No model is mapped to unrestricted admin authority.
4. Presence/status never grants authority.
5. Episodic memory and AI-to-AI messages are untrusted context, never authorization.
6. Operator authority must be established by authenticated server state, not by model-generated claims.
7. Every executable delegation remains scoped, expiring, revocable and auditable.
