# Discord MCP RC1: Multi-user compatibility contract
Status: design only, not deployed (2026-10-23).

## Requirements
- Public dashboard at discord.ailinux.me. All account authentication MUST go through the central AILinux login at https://login.ailinux.me; no separate Discord-MCP passwords or user database.
- Discord OAuth identity linking and independent bot installation authorization, with server/channel selection.
- Fully configurable per account: guild/channel allowlists, text and voice permissions, STT/TTS, wake-word, model/provider, session TTL, history and tool grants.
- One-time short-lived pairing code, OAuth2 PKCE and state validation, exact redirect URIs, encrypted server-side tokens and revocation.
- Default-deny authorization, Discord permissions plus AILinux scopes on every request, audit logs, replay/CSRF protection, rate limits, tenant isolation.
- Keep ALL RC0.5 tools: discord_voice_status/connect/join/say/listen/talk/exchange/leave; Discord /nova join/say/status/leave/quiet/listen/talk/history/forget; Whisper, Seraphina, voice history, separate wake/free-talk modes, restricted read-only MCP calls.
- Keep RC0.5 running as legacy adapter until an end-to-end tested replacement is ready; no downtime or lost features.
- Existing MCP OAuth uses env credentials and auto-authorization: DO NOT use it as a multi-user AILinux login. Validate and use the actual login.ailinux.me identity session/token mechanism; do not assume OIDC is available (/.well-known/openid-configuration currently returns HTTP 404). Define trusted token validation, issuer, audience, exp, logout/revocation and account subject mapping before implementation.
- Two-hour sessions: bot can remain connected, but MCP alone cannot autonomously reawaken the same GPT-6 browser session. Always display actual active provider/model.

## Central login flow
1. Unauthenticated discord.ailinux.me visits redirect to login.ailinux.me with verified return URL and state.
2. Login returns a verified AILinux account identity; discord.ailinux.me creates a scoped dashboard session without collecting account passwords.
3. Linking Discord via its own OAuth is separate from AILinux account login. All server installs and grants belong to the signed-in AILinux account.
4. Logout or revocation terminates dashboard sessions, pairing leases and MCP tokens as configured.
5. Discover and test the real login.ailinux.me SSO protocol before coding the adapter.

## Proposed routes (not implemented)
/                   dashboard
/mcp                authenticated MCP Streamable HTTP
/api/discord/oauth/start + callback
/api/discord/pair/create + redeem + revoke
/api/discord/servers
/api/discord/preferences
/api/discord/sessions

## Acceptance gates
1. Baseline tests and feature contract snapshot.
2. User identity, guild membership, bot install permissions and scope isolation tests.
3. Web-only onboarding for two independent users without CLI setup.
4. Negative tests: cross-account access, token replay, expired pairing, revoked grant, forged Discord interaction.
5. End-to-end Discord text, voice, STT, TTS, MCP, session reconnect and teardown tests.
6. External HTTPS/DNS health and rollback drill before enabling production.

## Rollback
RC1 must be deployable behind an independent feature flag and routing path. Revert its route to restore previous RC0.5 without altering legacy history, credentials or bot service.
