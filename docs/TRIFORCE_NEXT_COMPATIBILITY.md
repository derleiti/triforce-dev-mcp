# TriForce Next: compatibility and release gates

## Definition of done

A backend change is not complete until its supported clients are compatible, or
a tested compatible client release is published. Never silently change a
published version, artifact digest, public URL, wire schema or permission model.
Prefer additive changes, stable aliases and explicit deprecation windows.
Authorization stays server-side; client/version detection never grants rights.

## Transport and server contracts

- Canonical public/authenticated `/v1/mcp`: initialize, tools/list,
  tool calls, structuredContent, session lifecycle, streaming and guest RBAC.
- Handshake revisions: 2024-11-05, 2025-03-26, 2025-06-18, 2025-11-25.
- Stateless discovery revision 2026-07-28: server/discover, version metadata,
  current inventory discovery. Validate actual supported features, not only
  version negotiation.
- Legacy /v1/mcp/sse, /v1/sse, /v1/mcp/messages, /v1/messages, and trailing
  slash aliases: preserve existing client behavior.
- Authenticated /v1/mcp/service: reject anonymous and invalid credentials.
- Direct /mcp/claude, /mcp/codex, /mcp/gemini: retain legacy 2024-11-05 wire
  contract and canonical product version; do not advertise unsupported newer
  features. Verify auth and read/write authorization before expanding them.
- Public/local MCP, service and admin/dev tool surfaces, Discord transport,
  federation, native node/workspace bridge and Helper pairing must keep
  capability, identity, session and consent boundaries. Preserve legacy tool
  aliases used by released clients. Test discovery before and after pairing,
  read-only shares, write grants, disconnect/reconnect and denied mutations.

Server CI runs the protocol, actual HTTP route, auth, SSE, AICoder, workspace,
Helper and packaging contract tests. Test the full application and proxy
configuration separately on staging, including real transport disconnects.

## Client release matrix (baseline audited 2026-11-24)

| Product / platform | Published baseline | Required verification |
|---|---|---|
| TriForce backend | 2.86.8 (DEB 1:2.86.8-1) | Source and packaged service, federation, MCP |
| TriForce Control Center | DEB 1:2.86.8-2 | GUI/service status and settings, package upgrade |
| Helper WebMCP/PWA | helper release manifest | Browser permissions, pairing, cache, reconnect |
| Helper Linux AppImage / DEB | 2.90.40 web | Ordinary-user startup, pairing, upgrade, file permissions |
| Helper Android / Android TV | 2.90.38 | Signed APK, Android device/TV pairing and lifecycle |
| Helper Android arm64 capsule | 2.90.34 | Runtime/capabilities |
| Helper Windows installer / capsule | 2.90.34 | Native installation, startup, pairing |
| Helper macOS DMG / PKG / universal | 2.90.34 | Native startup, signing/notarization as applicable |
| Helper Arch / Linux binary / FreeBSD / OpenBSD | 2.90.34 | Platform package/runtime compatibility |
| Helper iOS / simulator | 2.90.34 | Unsigned IPA is not a signed production install; test appropriately |
| AICoder | 1.5.3 | Linux DEB/binary, Windows, macOS, Termux, AUR, account JWT and MCP |
| AILinux Client / Loom and Pocket AI | Inventory before each affected release | Shared MCP/tool, model and account contracts |

These are audited baseline versions, not a permanent version lock. Before
each release fetch the current tags, manifests, product pages and APT metadata.
Update compatibility pins and test existing installed versions as well as the
candidate; if a client cannot be tested, record that platform as unverified.

Published source references for the baseline:
- Helper: derleiti/ailinux-helper, d68e2b1d91df529cdd5e1b808f00549464786a19
- AICoder: derleiti/ai-coder, v1.5.3 (7cd55d91cc3ffdb93cb76155297be85940d5f29d)
- Helper source manifest: https://api.ailinux.me/v1/mcp/helper/release.json
- Public catalog: https://api.ailinux.me/v1/mcp/helper/releases

## Release gate

1. Record server/client versions, actual deployment and rollback points. Verify
   all affected repositories, pending changes and current compatibility pins.
2. Run server regressions and released-client source tests. Test actual
   end-to-end client-to-candidate connection and real native platforms as
   applicable; source tests alone do not prove native compatibility.
3. Build only affected packages. Verify package metadata, dependencies,
   permissions, signatures, hashes and non-root installation/startup.
   Test clean install and upgrade from published versions in disposable VMs or
   test devices. Test rollback. Never overwrite an immutable release artifact.
4. Publish verified artifacts and release manifest together. Verify direct
   downloads, content length and SHA-256, legacy download aliases, and
   corresponding release assets.
5. Update the relevant product pages and downloads:
   - https://ailinux.me/mcp-server/
   - https://ailinux.me/ai-coder/
   - https://ailinux.me/produkte/
   - https://ailinux.me/downloads-2/
   Discover additional product-specific pages for affected clients.
6. For Debian packages, publish through the existing validated signed APT
   generation pipeline at https://repo.ailinux.me/. Check signed metadata,
   every referenced package and checksum, version/epoch/architecture, public
   download and apt install/upgrade. Do not modify production APT in place
   without a rollback generation. Cross-check direct DEB and APT versions.
7. Stage the backend rollout across federation nodes; verify public and
   authenticated routes, actual Helper pairing, AICoder login/discovery, API
   health, logs, and client reconnect. Release only after gates pass.

## Open findings from the baseline audit

- The published Helper catalog offers Linux DEB 2.90.40, but the public
  resolute amd64 APT index contains only ailinux-helper 2.90.25.
  The published 2.90.40 DEB has root-owned directories with mode 0750 and
  app.asar with mode 0640, and postinst does not repair these application
  permissions. Do not promote that artifact into APT unchanged.
  Fix the build permissions, assign a new version, test a normal-user install
  and upgrade, regenerate hashes/manifest and publish both channels together.
- AICoder 1.5.4 exists as release preparation; 1.5.3 remains the published
  baseline until a verified newer release exists.
- All 16 Helper artifact files matched the published sizes and SHA-256 values
  on the server. 94 released Helper desktop source tests and 53 released
  AICoder MCP source tests passed in isolated local runs. These are not native
  runtime or cross-version end-to-end release certifications.
- Production /v1/mcp responded successfully to all four handshake versions
  and stateless discovery. Anonymous service/direct agent routes returned 401.
  Legacy/direct agent modernization beyond the existing contract remains open.
