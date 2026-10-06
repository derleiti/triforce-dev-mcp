# FOLGE-AUFTRAG — TriForce /v1/mcp + AILinux Helper 2.90.29 + Android Canary

Arbeite autonom am bestehenden AILinux/TriForce/AILinux-Helper-System weiter. Dies ist ein IMPLEMENTIERUNGS-, DEBUGGING-, RELEASE- UND LIVE-DEVICE-TASK, keine reine Beratung. Antworte Deutsch, per du, direkt. Frage nur bei echten Sicherheits-/Berechtigungsgrenzen nach. Keine Secrets, Tokens, Lease-IDs, Keystore-Daten oder Verifikationscodes ausgeben.

## KANONISCHE PROJEKTE

TriForce: `/home/zombie/workspace/triforce` → GitHub `derleiti/triforce-dev-mcp`, Branch `master`.
AILinux Helper: `/home/zombie/workspace/ailinux-helper` → GitHub `derleiti/ailinux-helper`, Branch `master`.
Produktiver TriForce läuft aus `/home/zombie/workspace/triforce`.

## MCP-SERVER-HOWTO — IMMER BEACHTEN

1. Immer zuerst `workspace_status` prüfen. `connected=true` bedeutet nur Lease vorhanden. Für lokale Ausführung müssen zusätzlich `transport_state=online` und `executor_online=true` sein.
2. Die live gemeldeten `capabilities` sind autoritativ. Android `computer_observe`, `computer_input` und `app_ops` existieren nur, wenn Computer Control vom User freigegeben UND der Android AccessibilityService ready ist.
3. Pair-Code = kurzlebiger Bootstrap. Nach erfolgreichem Pairing muss der Helper das vom Server gelieferte Resume-Credential dauerhaft speichern und bei App-/Netz-/Backend-Neustart damit resumieren. Nicht reflexartig neu pairen.
4. Bei Android-Steuerung immer: `computer_observe` → semantisches `target_id`/invoke → erneut `computer_observe`. Target-IDs sind scene-lokal und nach UI-Wechsel neu aufzulösen.
5. Live-Canary ist ausschließlich das alte Android-9-Gerät **ZTE 2050**. Den Pixel 10 NICHT anfassen. Vor Mutation Gerät über Client-Info/Serverlog eindeutig verifizieren.
6. Nach Restart/Reconnect erst `workspace_status`, dann einen harmlosen Observe/Read-Smoke, dann Mutation.
7. Keine identischen Retry-Loops. Bei Fehler erst neue Evidenz sammeln oder Hypothese/Layer ändern.
8. Niemals Workspace-/Resume-Tokens, Lease-IDs, Pairing-Internals, Passwörter, API-Keys, Keystore-Informationen oder Auth-Codes im Chat ausgeben.

## ABGESCHLOSSEN — RELEASE 2.90.29

AILinux Helper **2.90.29** ist gebaut, gepusht und veröffentlicht.

Helper Commit: `01cbc87` — `release: harden workspace reconnect in Helper 2.90.29`
TriForce Commits:
- `7e3e8f30` — `fix: harden MCP workspace resume and Helper 2.90.29`
- `79934fe4` — `release: publish Helper 2.90.29 platform downloads on MCP page`
Tag: `v2.90.29`
GitHub Release: `https://github.com/derleiti/ailinux-helper/releases/tag/v2.90.29`

### Implementierte Fixes

- Android Application-Heartbeat alle 10 Sekunden.
- Mehr als 45 Sekunden ohne eingehende Servernachricht → Socket wird verworfen und automatisch reconnectet.
- `ConnectivityManager.onLost()` reconnectet sofort.
- Accessibility-/Computer-Control-Readiness wird während aktiver Verbindung überwacht. Änderung löst Reconnect + neue Capability-Annonce aus.
- `nativeShareProfile()` unterscheidet `requested`, `accessibility_ready`, `control`.
- Android Resume-Credential wird atomar/synchron gespeichert; Bootstrap-Pair-Code wird in derselben SharedPreferences-Transaktion entfernt.
- TriForce `reconnect_web_workspace()` gibt das persistierte Resume-Credential beim Reconnect wieder zurück.
- `/v1/mcp/node/connect` sendet Resume-Credential bei Reconnect an den Helper.
- `/v1/mcp` Helper-Download-Routen: fehlender `HTTPException`-Import gefixt.
- `app/routes/mcp.py` und `app/routes/mcp_node.py` von versehentlichem executable-Mode auf 0644 korrigiert.
- Browser/PWA Helper auf `2.90.29-browser`, Cache/Manifest-Buster `29029`.
- MCP Initialize-Instructions enthalten jetzt das Workspace-Howto oben.
- Helper Version `2.90.29`, Android `versionCode 35`.

### Audit / Tests

- MCP `/v1/mcp`, `mcp_node`, Workspace-Session-Code, Android Helper und Desktop Helper auditiert.
- Nach Fixes keine error/critical Bug-/Security-Findings in den geprüften MCP-Routen.
- Frühere restliche MCP-Hinweise waren überwiegend Style/Modernisierung: broad exception catches, alte `typing`-Syntax, Import-Sortierung; nicht release-blockierend.
- Android/desktop Audit: keine warning/error Findings.
- TriForce fokussiert: 101 Tests passed; letzter Release-Surface-Smoke zusätzlich 23/23 passed.
- Helper Desktop: 69/69 Tests passed; npm audit: 0 vulnerabilities.
- Android: `:app:testDebugUnitTest :app:assembleDebug` BUILD SUCCESSFUL.
- Produktions-Android-APK lokal mit dem bestehenden Release-Signer gebaut; Signer stimmt mit 2.90.28 überein; APK Signature Scheme v2/v3 verifiziert. Keine Signer-/Keystore-Geheimnisse ausgeben.
- `git diff --check` sauber.
- Recovery Snapshot: `/home/zombie/workspace/.workspacebackup/retry-20260914-183651/`.

### GitHub Actions / Artefakte

Für `v2.90.29` erfolgreich:
- `AILinux Helper` Run `34870402238` — success.
- `Platform Artifacts` Run `34870402309` — success.
- `Platform package contract` Run `34870402247` — success.

GitHub Release enthält native Helper-Pakete und Plattform-Artefakte für Android, Linux, Windows, macOS, Arch, FreeBSD, OpenBSD und unsigned iOS/simulator sowie Checksums.

### ailinux.me / Web MCP

`https://api.ailinux.me/v1/mcp/helper/releases` liefert live `latest_version: 2.90.29` und veröffentlicht:
- Android APK + Android arm64 artifact
- Linux AppImage, DEB, binary
- Windows native EXE + platform artifact
- macOS DMG, PKG, universal artifact
- Arch package
- FreeBSD
- OpenBSD
- iOS unsigned IPA + simulator ZIP

`GET https://api.ailinux.me/v1/mcp/helper/android` liefert HTTP 200 und Header `x-ailinux-helper-version: 2.90.29`.
Die `/v1/mcp` Webapp zeigt 2.90.29 und die zusätzlichen Plattformdownloads im „All downloads“-Bereich.

## NOCH OFFEN — PRIORITÄT

### P0 — Live ZTE mit 2.90.29 aktualisieren und Reconnect wirklich beweisen

- Exakt ZTE 2050 / Android 9 verifizieren.
- Helper 2.90.29 installieren/aktualisieren.
- Pairing nur falls wirklich nötig. Danach Resume-Credential muss persistent sein.
- Tests: App schließen/backgrounden, Screen aus/an, Netz kurz verlieren/wiederkommen, TriForce Backend restart.
- Nach jedem Test prüfen: gleiche Lease/Session resumiert, Executor kommt selbstständig zurück, Pair-Code bleibt nicht als notwendiges Credential übrig, keine neue Pair-ID ohne echten Expiry/Revoke.
- Logs auf `transport_ping_timeout`, Resume 4xx, Reconnect-Loops und stale inbound prüfen.

### P0 — Device-Control Capability Rebind

- Accessibility an → innerhalb ca. 10–30 Sekunden müssen `computer_observe`, `computer_input`, `app_ops` wieder in `workspace_status.capabilities` erscheinen.
- Accessibility aus → Tools müssen fail-closed verschwinden.
- Accessibility wieder an → Tools müssen automatisch zurückkommen.
- Observe/Input/App-Launch danach live testen.

### P0 — Ursprünglichen Nova-Profilbild-Task fertigstellen

Auf dem ZTE liegt bereits `Pictures/AILinux/nova-avatar.b64`.
- Datei in echte `nova-avatar.jpg` oder PNG dekodieren.
- Instagram `nova.ailinux.me` öffnen.
- Profil bearbeiten → Profilbild setzen.
- Vorher ZTE eindeutig verifizieren.
- Keine zusätzlichen Posts, DMs, Follows oder Invites ohne expliziten Auftrag.

### P1 — Vollständiger ZTE Device-Smoke

Observe → semantischer Tap/Invoke → Type → Swipe-Alias → Wake → App launch → Standby/Wake → Backend restart/reconnect. Nach jedem UI-Wechsel neu observieren und Target neu auflösen.

### P1 — Logs nach Live-Test

TriForce MCP/system logs nach neuen Warnungen/Fehlern durchsuchen: Transport-Ping-Timeout, Capability-Flapping, Workspace-Resume-Fehler, tool-stage Errors, reconnect storms. Nur reproduzierte Fehler patchen; Regressionstest dazu.

### P1 — GitHub TriForce CI/Security startup_failure untersuchen

Die aktuellen TriForce Push-Runs `CI` und `Security` enden als `startup_failure` ohne Jobs/Logs. Das war auch beim vorigen Commit bereits so. Ursache separat auf GitHub Workflow-/Repository-Ebene prüfen (Workflow-Syntax/Repo Actions Policy/Runner-/Billing-/GitHub-seitiger Startfehler), nicht blind Code ändern. Lokale Tests sind grün.

### P2 — Working Tree Hygiene

TriForce hat aktuell das bereits vorher existierende untracked `app/static/nova/.keep`. Nicht automatisch committen oder löschen; zuerst klären, ob es zum Nova-Webasset-Task gehört. Helper Working Tree soll sauber sein.

## AKZEPTANZ

Der Folge-Task ist abgeschlossen, wenn:
1. ZTE läuft auf Helper 2.90.29.
2. App-/Netz-/Backend-Unterbrechungen resumieren ohne manuelles Neupairing.
3. Accessibility Capability-Rebind ist live bewiesen.
4. Device-Control-Smokes funktionieren stabil.
5. Nova-Profilbild ist auf `nova.ailinux.me` gesetzt.
6. Nach den Live-Tests gibt es keine ungeklärten neuen Connection-/MCP-Fehler.
7. TriForce CI/Security startup_failure ist entweder behoben oder mit externer, reproduzierbarer Ursache sauber dokumentiert.
8. Git-Status ist sauber bis auf bewusst erhaltene unrelated/untracked Dateien.
