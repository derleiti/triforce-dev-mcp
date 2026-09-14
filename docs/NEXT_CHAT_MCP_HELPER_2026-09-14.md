# FOLLOW-UP PROMPT — TriForce /v1/mcp + AILinux Helper 2.90.29 + Android Canary

Arbeite autonom am bestehenden AILinux/TriForce/AILinux-Helper-System weiter. Dies ist ein Implementierungs-, Debugging-, Release- und Live-Device-Task, keine reine Beratung. Antworte Deutsch, per du, direkt. Keine Secrets/Tokens/Lease-IDs ausgeben.

## KANONISCHE PROJEKTE

TriForce: `/home/zombie/workspace/triforce` → `derleiti/triforce-dev-mcp`, Branch `master`.
AILinux Helper: `/home/zombie/workspace/ailinux-helper` → `derleiti/ailinux-helper`, Branch `master`.
Produktiver TriForce läuft aus `/home/zombie/workspace/triforce`.

## MCP-SERVER-HOWTO

1. Immer zuerst `workspace_status` prüfen. `connected=true` reicht NICHT: für lokale Ausführung müssen `transport_state=online` und `executor_online=true` sein.
2. Live-`capabilities` sind autoritativ. Android `computer_observe`, `computer_input`, `app_ops` existieren nur bei aktivem Computer-Control-Grant UND ready AccessibilityService.
3. Pair-Code = kurzlebiger Bootstrap. Nach erfolgreichem Pairing muss der Helper den serverseitigen Resume-Credential dauerhaft speichern und beim Neustart/Backgrounding damit resumieren. Nicht reflexartig neu pairen.
4. Android UI: `computer_observe` → semantisches `target_id`/invoke → erneut observe. Targets nach jedem UI-Wechsel neu auflösen.
5. Kein Pixel 10 anfassen. Live-Canary ist das alte Android-9-Gerät ZTE 2050. Gerät vor Mutation anhand Serverlog/Client-Info eindeutig verifizieren.
6. Bei Logs/Toolfehlern erst Ursache ändern oder neue Evidenz sammeln; keine identischen Retry-Loops.
7. Nie Workspace-/Resume-Tokens, Lease-IDs, Keystore-Daten, Passwörter oder Verifikationscodes im Chat ausgeben.

## ERLEDIGTER STAND

AILinux Helper 2.90.28 war zuvor produktiv veröffentlicht. Im aktuellen Task wurden zusätzliche Fixes für 2.90.29 implementiert:
- Android Application-Heartbeat alle 10 Sekunden; >45 Sekunden ohne eingehende Servernachricht verwirft Socket und reconnectet.
- `ConnectivityManager.onLost()` reconnectet sofort.
- Accessibility-/Computer-Control-Readiness wird während der Verbindung überwacht; bei Änderung reconnectet der Helper und annonciert die Capability-Menge neu.
- `nativeShareProfile()` unterscheidet `requested`, `accessibility_ready`, `control`.
- Android Resume-Credential wird atomar/synchron gespeichert und Bootstrap-Pair-Code in derselben SharedPreferences-Transaktion entfernt.
- TriForce `reconnect_web_workspace()` liefert den bereits persistierten Resume-Credential wieder zurück; `/v1/mcp/node/connect` sendet ihn beim Reconnect an den Helper.
- `/v1/mcp` hatte in Helper-Download-Routen `HTTPException` benutzt ohne Import; gefixt.
- `app/routes/mcp.py` und `mcp_node.py` waren versehentlich executable ohne shebang; Modus auf 0644 korrigiert.
- Browser/PWA Helper auf `2.90.29-browser`, PWA Cache/Manifest-Buster `29029`.
- MCP-Initialize-Instructions enthalten jetzt ein kurzes Workspace-Server-Howto (Status vs Transport/Executor, Pair vs Resume, Android Accessibility, Observe/Invoke/Observe, Secrets).
- Helper Version auf 2.90.29 / Android versionCode 35 angehoben.

Verifikation vor Release:
- TriForce fokussiert: 101 Tests passed (`test_mcp_workspace_capability.py`, `test_mcp_agent_instructions.py`, `test_mcp_public_workspace_surface.py`).
- Helper Desktop: 69 Tests passed, npm audit 0 vulnerabilities.
- Android: Gradle `:app:testDebugUnitTest :app:assembleDebug` BUILD SUCCESSFUL.
- Helper asset checks + beide `git diff --check` sauber.
- Codeaudit Android/desktop: keine gemeldeten warning/error findings.
- Codeaudit MCP `/v1/mcp`/`mcp_node`: nach den Fixes keine error/critical bug/security findings; verbleibende frühere Hinweise waren überwiegend Style/Modernisierung (`typing`-Syntax, broad exception catches), nicht release-blockierend.
- Recovery snapshot: `/home/zombie/workspace/.workspacebackup/retry-20260914-183651/`.

## OFFEN / NÄCHSTE AKTIONEN

A. Release abschließen: Diffs prüfen, ausschließlich beabsichtigte Dateien stagen (insbesondere `app/static/nova/.keep` nur übernehmen, wenn wirklich zum Task gehörig; sonst NICHT committen), Helper + TriForce committen/pushen, Helper `v2.90.29` taggen/pushen.
B. GitHub Actions überwachen: `AILinux Helper`, `Platform Artifacts`, `Platform package contract` erfolgreich abschließen lassen. Explizit den Repo-Platform-Artifacts-Run für `v2.90.29` ausführen/verifizieren.
C. GitHub Release `v2.90.29` vollständig veröffentlichen. Erwartet mindestens Android APK, Linux DEB/AppImage, Windows EXE, macOS DMG/ZIP + Checksums. Production-Android-Signing verifizieren, keine Keystore-Secrets ausgeben.
D. Release-Assets nach `/home/zombie/workspace/triforce/releases/helper/` spiegeln, `AILinux-Helper-latest.*` Aliases auf 2.90.29 aktualisieren, Checksums übernehmen, TriForce neu laden/restarten und live `/v1/mcp/helper/releases` prüfen. `/v1/mcp` muss 2.90.29 als aktuelle Helper-Version erkennen und Android-Download HTTP 200 liefern.
E. Live ZTE-Canary auf Helper 2.90.29 aktualisieren und exakt verifizieren. Nach App-Schließen/Backgrounding/Screen-off testen: Lease bleibt, Resume-Credential bleibt, Executor self-healt, keine neue Pair-ID ohne echten Ablauf/Revoke.
F. Android Device-Control-Rebind testen: Accessibility an → binnen ca. 10–30 s `computer_observe/computer_input/app_ops` wieder annonciert; Accessibility aus → Capabilities verschwinden fail-closed; wieder an → kommen zurück. Logs als Evidenz sichern.
G. Danach ursprünglichen UX-Task fertigstellen: auf dem ZTE liegt `Pictures/AILinux/nova-avatar.b64`; in echte Bilddatei (`nova-avatar.jpg`/PNG) dekodieren und für Instagram-Account `nova.ailinux.me` als Profilbild setzen. Vorher exakt ZTE verifizieren. Keine Posts/DMs/Follows zusätzlich ohne expliziten Auftrag.
H. Noch offene Device-Smokes: Observe → semantischer Tap/Invoke → Type → Swipe alias → Wake → App launch → Standby/Wake → backend restart/reconnect; nur ZTE.
I. Nach erfolgreicher Live-Verifikation relevante Logs auf neue `transport_ping_timeout`, Reconnect-Loops, Capability-Flapping, 4xx Pair/Resume-Fehler und MCP tool-stage Fehler prüfen. Nur reproduzierte Fehler patchen; Tests ergänzen.

## AKZEPTANZ

Task ist fertig, wenn Helper 2.90.29 auf GitHub und ailinux.me live ist, `/v1/mcp` die Version korrekt ausliefert, der ZTE nach App-/Netz-/Backend-Unterbrechung ohne manuelles Neupairing resumiert, Device-Control-Capabilities sich korrekt an Accessibility-Readiness anpassen, und das Nova-Profilbild auf Instagram gesetzt wurde. Danach Git-Status beider Repos sauber bzw. ausschließlich bewusst untracked/unrelated.
