# Umbau: CLI-Wrapper-Agents → AICoder-Instanzen, orchestriert von TriForce

Stand: 2026-09-10 · Status: Phase 4 Canary aktiv (`opencode-mcp`) · Verantwortlich: Markus / Nova

## Ziel

Die vier CLI-Wrapper-Agents (claude-mcp, codex-mcp, gemini-mcp, opencode-mcp)
werden durch AICoder-Instanzen mit unterschiedlichen Profilen ersetzt. TriForce
startet und überwacht sie, wertet strukturierte Events aus und meldet über
Notify und Mail.

## Befund (live geprüft 2026-09-10)

- Wrapper heute: `agent_controller.call_agent()` baut pro Agent-Typ eine eigene
  `bash -c`-Pipeline (claude-triforce, codex-triforce, agy-triforce,
  opencode-triforce) und liest die Textausgabe. Dazu `_inject_*_env`-Hacks
  pro Hersteller.
- Radius: 13 Dateien rufen Agents auf; die vier IDs stehen an >150 Stellen in
  12 Dateien fest im Code (shortcodes, notification_manager, tool_registry_v5,
  agent_spawner, group_chat, auto_evolve, …).
- AICoder 1.2.4 (Repo-Tag), installiert ist .deb 1.2.3.
- `aicoder agent "<prompt>" --json-events` liefert headless NDJSON. Getestet:
  tools_ready → run_start → model_start → model_response → tool_call →
  tool_result → performance_summary → final → run_terminal → result.
  Fehler kommen sauber als `error` + `run_terminal{status:failed}`.
- Headless-Rechte (aicoder/privileges.py, PrivilegeBroker.evaluate headless=True):
  - approval_mode=ask       → nur lesen
  - approval_mode=autopilot → schreiben im Workspace
  - löschen, sudo/Elevation, Security-Änderung, Workspace verlassen → immer
    abgelehnt ("headless mode fails closed")
- Isolation: config.py und audit.py nutzen Path.home() (`~/.config/ai-coder`),
  cli.py:369 und plugins.py nutzen XDG_CONFIG_HOME (`~/.config/aicoder`).
  Zwei Verzeichnisnamen. Eigenes HOME isoliert, KAPPT aber die Account-Provider
  (Hersteller-Logins in ~/.codex, ~/.claude, …).
- Account-Provider: account:chatgpt|claude|mistral|gemini/<modell>. Alle vier
  auf dem Hetzner verlinkt. account:chatgpt/gpt-6-astra lieferte am 2026-09-10
  früh 5× leere Antworten.
- Stolperfallen: Backend lauscht nur auf 172.17.0.1:9000 (nicht 127.0.0.1).
  Modellnamen veralten (mistral-code-agent-latest → 404).

## Architektur

1. **AgentType.AICODER** + ein Runner `app/services/aicoder_runner.py`:
   startet `aicoder agent --json-events`, parst NDJSON, liefert strukturiertes
   Ergebnis, bricht per Timeout sauber ab (Prozessgruppe killen).
2. **Profile** in `/var/tristar/agents/profiles/<id>.json`:
   id, rolle, system_prompt, model, fallback_model, approval_mode (ask|autopilot),
   enabled_tools (Whitelist), workspace, timeout, team_mode, notify-Regeln,
   target (local | remote-node:<host>).
3. **Instanz-Zustand** in `/var/tristar/agents/instances/<id>/home` (eigenes HOME,
   Hersteller-Logins als Symlinks). Kein Eingriff in den ai-coder-Code.
4. **Kompatibilität**: Die alten IDs bleiben Profilnamen. Umschaltung pro
   Agent über `runtime: legacy|aicoder` in agents.json. Rollback = Flag.
5. **Events → Status**: run_terminal/result → Agent-Status, Usage, Audit-Log.
   Fehler/Eskalation → notification_manager; Mail über mail_service nur nach
   Regeln (unten).

## Notify- und Mail-Regeln

| Ereignis | Notify | Mail |
|---|---|---|
| Lauf erfolgreich, < 10 min | – | – |
| Lauf erfolgreich, ≥ 10 min | normal | – |
| Lauf fehlgeschlagen | high | – (Sammelmail täglich) |
| gleiches Profil 3× hintereinander fehlgeschlagen | critical | sofort |
| Headless-Ablehnung (sudo/Löschen/Workspace verlassen) | high | – |
| Eskalation laut Arbeitsprotokoll §4 (Auth, Secrets, Zahlungswege, Datenverlust, Prod unten) | critical | sofort |

Cooldown pro Profil und Ereignistyp, damit nichts flutet.

## Phasen

### Phase 0 — Voraussetzungen
- [x] 0.1 Wrapper-Architektur und Aufrufer erfasst
- [x] 0.2 AICoder headless + Rechte-Mechanik geprüft
- [x] 0.3 Echter --json-events-Probelauf (mistral/codestral-latest, ok, 1,2 s)
- [x] 0.4 Core-venv angeglichen (2026-09-10 16:48): anthropic 1.0.0, openai 3.3.1,
      mistralai 2.9.3. Import-Test 269 Module + pytest 545/2 identisch zu vorher.
      Backend ruft Provider per httpx, nicht per SDK → Major-Sprünge ohne Laufzeitwirkung.
      Mitbetroffen: nova-flarum-bot, nova-log-monitor (gleiches venv).
      Altes venv: .venv-old-20260910-164818 · Rollback: .backups/venv-sync-*/venv-rollback.sh
- [x] 0.5 account:chatgpt diagnostiziert: Account/Auth gesund; veralteter/inkompatibler Modellname gpt-5.3-codex liefert sauber HTTP 400. Dynamischer Account-Katalog liefert gpt-6-astra, gpt-5.6-sol/terra/luna, gpt-5.5. Reale Smokes mit gpt-5.6-sol/terra/luna erfolgreich.
- [x] 0.6 AICoder 1.2.4 gepinnt auf dem Hetzner (.deb aus repo.ailinux.me),
      kein Dev-Klon für Prod. Headless-NDJSON-Smoke erfolgreich (2026-09-10).

### Phase 1 — Instanz-Isolation OHNE Änderung am ai-coder-Code (Vorgabe Markus 2026-09-10)
- [x] 1.1 Pro Instanz eigenes HOME: /var/tristar/agents/instances/<id>/home (Runner/Bootstrap implementiert, Pilot real erzeugt)
- [x] 1.2 Hersteller-Logins per Symlink einhängen (~/.codex, ~/.claude, ~/.vibe,
      ~/.gemini, Antigravity falls vorhanden) → Provider-owned Login-State bleibt im Original
- [x] 1.3 ~/.config/ai-coder pro Instanz aus Vorlage erzeugen (session.json +
      state.json mit Profilwerten); Instanzen teilen NIE state.json. Legacy XDG-Namespace ~/.config/aicoder wird separat angelegt.
- [~] 1.4 Isolierte Account-Provider geprüft: ChatGPT und Claude erfolgreich; Mistral funktioniert aus aktuellem AICoder-Source, aber das installierte 1.2.4-DEB zeigt noch Runtime/Packaging-Drift; Gemini/Antigravity noch nicht authentifiziert.

### Phase 2 — Runner + Pilot (TriForce)
- [x] 2.1 aicoder_runner.py + Unit-Tests für NDJSON, Isolation, Result-Normalisierung und Prozessgruppen-Timeout
- [x] 2.2 Pilotprofil `aicoder-review` (approval_mode=ask, nur lesen), neue ID; echter Run erfolgreich
- [x] 2.3 AgentController- und echter externer MCP-`agent_call`-Pfad verifiziert. Dynamische Profil-IDs sind im Tool-Schema erlaubt; Controller validiert weiterhin fail-closed.
- Abnahme: 10/10 echte Läufe ohne Hänger; semantische Evidence-Probleme des Review-Piloten reproduziert und Profil fail-safe gehärtet (bei nicht beweisbarer Aussage `NICHT VERIFIZIERT` statt Raten).

### Phase 3 — Notify + Mail
- [x] 3.1 Event-Mapping auf notification_manager: success/long-run, technical failure, repeated failure und headless deny getrennt.
- [x] 3.2 Mailregeln + Cooldown + tägliche Sammelmail: technische Fehler werden persistent gesammelt; Digest standardmäßig 08:00 Europe/Berlin; SMTP-Fehler bleiben retrybar; 3. technischer Fehler in Folge erzeugt einmalige Critical-Mail.
- Abnahme: fokussierte Notification/Digest-Tests grün; Headless-Denials erhöhen den technischen Failure-Streak nicht.

### Phase 4 — Migration der vier IDs (einzeln)
Reihenfolge nach Risiko: opencode-mcp → codex-mcp → claude-mcp → gemini-mcp (Lead)
- [~] `opencode-mcp`: Profil aktiv, `runtime: aicoder`, echter MCP Read- und Write-Smoke erfolgreich, on-demand `ready` ohne Legacy-PID; Canary-Beobachtung läuft.
- [~] `codex-mcp`: auf `runtime: aicoder` migriert; isolierter ChatGPT-Account-Smoke sowie echter MCP Read/Write-Smoke erfolgreich (`account:chatgpt/gpt-5.6-terra`).
- [~] `claude-mcp`: auf `runtime: aicoder` migriert; produktive Read/Write-Smokes erfolgreich über `openrouter/anthropic/claude-sonnet-5`. Native `account:claude/sonnet`-Route ist vorbereitet, derzeit aber durch providerseitiges Session-Limit blockiert.
- [~] `gemini-mcp`: AICoder-Profil vorbereitet und Read-Smoke erfolgreich über `openrouter/google/gemini-3.1-pro-preview`; produktive Umschaltung wieder auf `legacy` zurückgerollt, weil zwei Write-Smokes mit OpenRouter 402 scheiterten. Native Antigravity-Route verlangt neuen Login; Google AI Studio meldet 429 Prepayment-Credits depleted.

### Phase 5 — Aufräumen
- [ ] Wrapper-Skripte, `_inject_*_env`, Legacy-Zweige in call_agent entfernen
- [ ] Harte Agent-IDs schrittweise durch Profil-Lookup ersetzen

## Offene Punkte außerhalb des Umbaus
- GitHub Actions auf triforce-dev-mcp: "Actor is not allowed to trigger Actions
  workflows" (seit ≥ 2026-08-09). Kein CI-Sicherheitsnetz → lokale Tests sind das Tor.
- Tests mit hart verdrahtetem Pfad /home/zombie/triforce (CI-Runner scheitert).
- System-Collector liest bei jedem Start dieselben alten Syslog-Zeilen neu ein (Duplikate).
- baloo_file_extractor (KDE-Indexer) läuft auf dem Hetzner und scheitert am inotify-Limit.
