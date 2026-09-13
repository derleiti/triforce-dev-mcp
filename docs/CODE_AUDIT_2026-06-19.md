# TriForce Code-Audit vom 2026-06-19

## Kurzfazit

Der Backend-Code ist nach dem Patch vom 2026-06-19 deutlich stabiler: die zuvor roten Group-Chat-Tests sind gruen, `GET /v1/mcp` ist lokal per Regressionstest abgesichert, und externe MCP-Clients werden bei `tools/list` und `tools/call` gegen die vorhandene Allowlist geprueft. Die volle lokale Pytest-Suite ist jetzt gruen: `178 passed`.

Der Stand ist aber weiterhin keine perfekte Release-Baseline. Doku, Versionsnummern, Tool-Zaehler, Default-Modelle, Service-Units, Repo-Hygiene und Dependency-Reproduzierbarkeit driften noch auseinander. Diese offenen Punkte sollten separat gepatcht werden, damit kein breiter Infrastruktur- oder Produktentscheid in einen Sicherheits-/Bugfix-Patch gemischt wird.

Umgesetzt in diesem Patch:

1. `GET /v1/mcp` repariert: `mcp_health_or_sse` nutzt keinen undefinierten `params`-Namen mehr.
2. MCP-Allowlist im Hauptendpoint durchgesetzt: `tools/list` filtert externe Clients, `tools/call` blockt nicht erlaubte Tools.
3. Group-Chat-State-Pfad fehlertolerant gemacht: kein harter Schreibzwang nach `/var/tristar/group_chat` mehr.
4. `tools/upsonic_triforce.py` F821-Laufzeitfehler behoben.
5. Regressionstests fuer MCP-GET, externe Tool-Filterung, externe Tool-Blockade und Group-Chat-Persistenz laufen gruen.

Weiter offen:

1. Default-Modell-Policy zentralisieren: Agent-Policy fordert `ollama/gemma4:12b`, aber mehrere Codepfade defaulten weiter auf Gemini, DeepSeek oder GPT-OSS.
2. Service-Units und Startskripte konsolidieren.
3. Repo-Hygiene bereinigen: getrackte Runtime-/Build-Artefakte separat aus Git nehmen.
4. Ruff schrittweise als Gate nutzbar machen; global sind weiterhin viele Stil-/Altlasten vorhanden.
5. Dependency-Baseline reproduzierbar machen.

## Gepruefter Stand

- Datum: 2026-06-19
- Branch: `nova-nextlevel-20260603`
- Tracked files: `922`
- Tracked Python files: `335`
- Python files unter `app/`: `250`
- FastAPI-App importiert mit `399` Routen.
- Produktiver Service: `triforce.service` ist `active`.
- Arbeitsbaum war vor diesem Audit bereits dirty; vorhandene Aenderungen wurden nicht zurueckgedreht.

Dirty Worktree beim Audit:

- Backend/MCP: `app/mcp/handlers_v4.py`, `app/mcp/handlers_wordpress.py`, `app/mcp/structured_admin.py`, `app/mcp/tool_registry_v4.py`, `app/routes/chat.py`, `app/routes/mcp.py`, `app/routes/openai_compat.py`, `app/routes/settings.py`, `app/routes/text_analysis.py`, `app/services/tristar/agent_controller.py`
- Docker/WordPress/Scripts: mehrere Dateien unter `docker/`, `scripts/`
- Untracked: `config/users.json.backup-before-testpurchase-20260613-062605`

## Was funktioniert

### Service und Basis-API

- `systemctl is-active triforce.service` meldet `active`.
- `GET http://127.0.0.1:9000/health` liefert `{"ok":true,"status":"ok"}`.
- `GET /v1/.well-known/mcp` liefert MCP-Metadaten mit `streamable-http`, Endpoint `/v1/mcp` und Protocol Versions `2024-11-05`, `2025-03-26`.
- `GET /v1/mcp/status` funktioniert und meldet:
  - `status: ok`
  - `205` registrierte MCP-Methoden
  - `model_count: 743`
- `POST /v1/mcp` mit `tools/list` funktioniert und liefert im laufenden Dienst `145` Tools.
- `GET /v1/models` funktioniert und meldet `total: 742`, `excluded: 1`, `filtered: true`.

### Build- und Testsignale

- Syntax/Bytecode: `.venv/bin/python -m compileall app tests scripts tools -q` ist gruen.
- Gesamte Pytest-Suite: `178 passed`.
- MCP/Group-Chat/Security-Fokus: `67 passed`.
- Security-Subset: in der vollen Suite gruen; die getrackte Datei `tests/test_security_findings.py` enthaelt jetzt auch Runtime-Tests fuer `GET /v1/mcp`, `tools/list` und `tools/call`.
- Registry/Structured-Admin-Subset: `15 passed`.
- `pip check`: `No broken requirements found`.
- `ruff --select F821 tools/upsonic_triforce.py`: gruen.

### Architektur, die aktuell nutzbar ist

- `app/main.py` baut einen grossen FastAPI-Monolithen mit Routern fuer Admin, Agents, Chat, Crawler, MCP, Models, OpenAI-Kompatibilitaet, RAG, Vision, TriStar, Mesh/Federation, Client APIs und Nova-Flows.
- Lifespan initialisiert Redis Rate Limiter, Model Registry Refresh, TriStar Services, Logging, Mesh Coordinator, Federation, Distributed Compute, MCP Brain, MCP WebSocket und System-Log-Collector. Viele Subsysteme sind defensiv mit `try/except` gekapselt, daher startet der Dienst auch bei optionalen Ausfaellen weiter.
- `app/services/model_registry.py` konsolidiert dynamische Provider-Discovery und statisch dokumentierte Hosted Models. Die Live-API liefert mehrere hundert Modelle.
- `app/routes/chat.py` und `app/services/openai_compat.py` validieren Modelle vor Streaming, wodurch der fruehere "404 nach Stream-Start"-Fehler weitgehend vermieden wird.
- `app/routes/health.py` ist klein, stabil und fuer Monitoring geeignet.
- `app/mcp/tool_registry_unified.py` erzeugt eine deduplizierte Tool-Sicht; ohne Extra-Schemas sind lokal `124` Tools sichtbar, im Live-Endpoint mit Extras `145`.
- Structured Admin Read-only Tools, MCP-Telemetrie und Write-Fallback sind registriert und durch Tests abgedeckt.

## Was nicht funktioniert oder riskant ist

### Gepatcht: `GET /v1/mcp`

Bisheriges Symptom:

- `GET http://127.0.0.1:9000/v1/mcp` liefert aktuell HTTP 500.
- Journal zeigt: `NameError: name 'params' is not defined`.
- Ursache: `app/routes/mcp.py` im Handler `mcp_health_or_sse` nutzt `params.get(...)`, obwohl der GET-Handler keine Variable `params` definiert.

Umgesetzter Patch:

- `mcp_health_or_sse` nutzt jetzt `request.query_params`, den Header `Mcp-Protocol-Version` oder den Default `2024-11-05`.
- Regressionstest: `test_get_mcp_health_does_not_reference_missing_params`.

### Gepatcht: MCP-Allowlist im Hauptendpoint

Bisheriger Befund:

- `app/utils/mcp_security.py` definiert `EXTERNAL_TOOL_ALLOWLIST_FULL`, `EXTERNAL_TOOL_ALLOWLIST_REMOTE`, `PRIVILEGED_TOOLS`, `is_tool_allowed()` und `filter_tools_for_external()`.
- `app/routes/mcp.py` definiert nur No-op-Kompatibilitaetshooks `_maybe_block_write_tool()` und `_filter_tools_for_client()`.
- `handle_tools_list()` und `handle_tools_call()` bekommen keinen `Request` und wenden `is_tool_allowed()` nicht an.
- Die Security-Tests pruefen die Helper und einzelne Group-Chat-CLI-Blocks, aber nicht die Runtime-Durchsetzung fuer `tools/list`/`tools/call`.

Risiko:

- Externe, authentifizierte MCP-Clients koennen potentiell mehr Tools sehen oder aufrufen als die Allowlist vorsieht.
- Besonders kritisch sind Tools wie `shell`, `binary_exec`, `custom_exec`, `code_edit`, `code_patch`, `service_control`, `container_control`, `remote_exec`, `config_set`, `vault_add`, `mail_send`, WordPress-Write-Tools und Agent-Lifecycle-Tools.

Umgesetzter Patch:

- `handle_tools_list(params, request=None)` filtert externe Clients per `filter_tools_for_external()`.
- `handle_tools_call(params, request=None)` prueft vor Dispatch per `is_tool_allowed()`.
- Interne Direktaufrufe ohne `request` bleiben fuer bestehende Unit-Tests und interne Pfade kompatibel.
- Tool-Handler erhalten `request` nur, wenn ihre Signatur diesen Parameter akzeptiert.
- Externe Blockaden liefern einen klaren MCP-Tool-Fehler mit `code: MCP_TOOL_FORBIDDEN`.
- Regressionstests: externe Tool-Liste enthaelt erlaubte Tools wie `chat` und `group_chat_create`, aber kein `agent_start`; externer `agent_start`-Call wird blockiert.

### Gepatcht: Group-Chat State-Pfad

Bisherige Pytest-Fehler:

- `tests/test_group_chat_hardening_unittest.py::test_create_session_clones_default_participants`
- `tests/test_group_chat_hardening_unittest.py::test_post_message_updates_only_current_session_participant_state`
- `tests/test_mcp_swarm_runtime_contract_unittest.py::test_tools_call_dispatches_group_chat_create`

Ursache:

- `app/services/group_chat.py` setzt beim Import `GROUP_CHAT_DIR = Path("/var/tristar/group_chat")` und schreibt Sessions direkt dorthin.
- In Sandbox/CI ist `/var/tristar/group_chat` nicht beschreibbar: `OSError: [Errno 30] Read-only file system`.

Umgesetzter Patch:

- `GROUP_CHAT_DIR` wird aus `TRISTAR_GROUP_CHAT_DIR` oder `TRISTAR_STATE_DIR` abgeleitet; Default bleibt `/var/tristar/group_chat`.
- Es wird nicht mehr beim Modulimport nach `/var/tristar/group_chat` geschrieben.
- Beim Speichern wird ein beschreibbarer Pfad sichergestellt; bei `OSError` wird auf `TRISTAR_FALLBACK_STATE_DIR` oder `/tmp/tristar/group_chat` gewechselt und einmal erneut geschrieben.
- Die zuvor roten Group-Chat-Tests laufen gruen.

### P1: Default-Modell-Policy driftet

Policy in `AGENTS.md` und README-Statusblock:

- Default server chat model: `ollama/gemma4:12b`
- Local Ollama tag: `gemma4:12b`
- OpenClaw und AI-Coder sollen auf `ollama/gemma4:12b` bleiben.

Abweichende Codepfade:

- `app/config.py`: `ollama_fallback_model = "gpt-oss:20b-cloud"`
- `app/routes/mcp.py`: `handle_llm_invoke` defaultet ohne Modell auf `gemini/gemini-2.0-flash`
- `app/routes/client_chat.py`: Client-Default ist `ollama/deepseek-v3.1:671b-cloud`
- `app/services/user_tiers.py`: Free/Ollama-Liste beginnt mit DeepSeek Cloud-Modellen
- `config/triforce.env.template`: `DEFAULT_CHAT_MODEL=gemini/gemini-2.0-flash`, `OLLAMA_DEFAULT_MODEL=qwen2.5:14b`
- `app/services/tristar/settings_controller.py`: mehrere Defaults und Fallbacks zeigen auf Gemini/Qwen/Mistral

Patch:

- Eine kanonische Einstellung einfuehren, z. B. `DEFAULT_CHAT_MODEL=ollama/gemma4:12b`.
- Alle Chat-/MCP-/Client-/AI-Coder-Defaults aus dieser Einstellung lesen.
- Explizite Alternativen weiter erlauben, aber keine stillen Default-Wechsel.
- Test: ohne Modell muss `llm.invoke`, `/v1/client/chat` und der OpenAI-kompatible Pfad dieselbe konfigurierte Default-Route verwenden.

### P1: Service- und Startskripte widersprechen sich

Befund:

- README/AGENTS nennen Produktion auf Port `9000`, Dev teils `9100`.
- `scripts/systemd/triforce.service` nutzt `/home/zombie/workspace/triforce` und `scripts/start-backend.sh`.
- `config/triforce.service` zeigt auf `/home/zombie/workspace/triforce/backend`, `config/.env` und Port `${TRIFORCE_API_PORT:-9100}`.
- `debian/triforce-backend.service` zeigt auf `/opt/triforce`, `--workers 2`.
- `scripts/start-backend.sh` startet auf `0.0.0.0:9000`, berechnet Worker aus CPU-Kernen und startet optional Redis/Ollama.
- `start-triforce.sh` startet ebenfalls Uvicorn auf Port `9000`, aber mit anderer Update-/Env-Logik.

Patch:

- Eine produktive Unit als Quelle der Wahrheit markieren.
- Alte Unit-Dateien entweder entfernen, in `docs/legacy/` verschieben oder deutlich als Template markieren.
- README, Makefile und AGENTS auf denselben Port-/Service-Stand bringen.

### P1: Repo enthaelt getrackte Runtime-/Build-Artefakte

Befund:

- `.gitignore` schliesst inzwischen u. a. `docker/repository/`, `docker/wordpress/`, `docker/mailserver/`, `build/`, `data/rag/`, `.backups/`, `tests/` aus.
- Trotzdem sind viele Artefakte schon getrackt, z. B.:
  - `build/debian-pkg/...`
  - `client-deploy/debian-build/...`
  - `data/n8n/database.sqlite*`
  - `docker/repository/log/update-mirror.log.old`
  - `docker/wordpress/.cf_token`
  - Backup-Dateien unter `docker/wordpress/apache/...`
  - `.backup_mcp_hook_20260315_152403/app/routes/mcp.py`
- Neue Tests wuerden wegen `tests/` in `.gitignore` leicht uebersehen, obwohl bestehende Tests getrackt sind.

Patch:

- Separaten Hygiene-PR machen, nicht mit Feature-Code mischen.
- Mit expliziten Pathspecs `git rm --cached` fuer Runtime-/Build-Artefakte.
- `tests/` aus `.gitignore` entfernen, falls Tests weiter versioniert werden sollen.
- Vor Loeschungen Import-/Referenzsuche und `compileall` laufen lassen.

### P2: Ruff ist kein brauchbares Gate

Befund:

- `.venv/bin/ruff check app tests scripts tools` meldet `741` Findings.
- Der Grossteil sind Stilthemen: unused imports, E402, f-strings ohne Platzhalter, bare `except`.
- Der konkrete F821-Bug in `tools/upsonic_triforce.py` ist gepatcht: `data["error"]` statt `data[error]`.
- Gezielter Check: `.venv/bin/ruff check tools/upsonic_triforce.py --select F821` ist gruen.

Patch:

- Ruff-Konfiguration einfuehren oder eingeschraenkt starten, z. B. erst `F821`, `E9`, `F401` auf `app/` und `tools/`.
- Weitere echte Fehler zuerst beheben, Stilbereinigung separat.

### P2: Dependency-Baseline ist nicht reproduzierbar

Befund:

- `pip check` ist in der aktuellen venv sauber.
- Die installierte venv weicht aber von `requirements.txt` ab, z. B. FastAPI, Pydantic und Black.
- Tests beweisen daher den aktuellen lokalen Zustand, nicht zwingend einen frischen Install aus `requirements.txt`.

Patch:

- Lock-/Constraints-Datei erzeugen oder `requirements.txt` auf die tatsaechlich getestete Versionsebene aktualisieren.
- CI sollte `python -m venv`, `pip install -r requirements.txt`, `compileall` und Pytest in einem frischen Env ausfuehren.

### P2: Client-Chat hat Routing-Inkonsistenzen

Befund:

- `/v1/client/chat` routet Guest/Registered zu Ollama, Pro/Enterprise zu Ollama oder OpenRouter.
- `get_client_tier()` meldet fuer alles ausser Guest `backend="openrouter"`, obwohl Registered laut Code weiter Ollama nutzt.
- `analyze_file()` nutzt fuer alle Nicht-Guests `call_openrouter(normalize_openrouter_model(model))`; der Default kommt aber aus `get_default_model()` und ist `ollama/deepseek-v3.1:671b-cloud`.

Patch:

- Registered in Status/Analyze konsistent als Ollama behandeln.
- Provider-Routing fuer Analyse wiederverwenden statt Sonderlogik.

## Doku-Drift

Aktuell widersprechen sich mehrere Dokumente und Kommentare:

- `app/config.py` sagt Version `2.81`.
- MCP-Kommentare/Logs nennen v2.80, v2.82, v2.86, v2.90, v3, v4, v5.
- README zeigt v2.80 und `134` MCP Tools.
- Live `tools/list` liefert `145` Tools.
- `app/routes/mcp.py` registriert `205` MCP Handler.
- `GET /v1/mcp/status` meldet `model_count: 743`, `/v1/models` meldet `total: 742`, `excluded: 1`.
- AGENTS/README-Statusblock sagt Default-Modell `ollama/gemma4:12b`, mehrere Codepfade verwenden andere Defaults.

Patch:

- Eine generierte Statusquelle definieren, z. B. `docs/AGENT_SYSTEM_STATUS.md` oder ein `scripts/status-doc.py`.
- README nur aus dieser Quelle aktualisieren.
- Tool-Zahlen nicht statisch pflegen oder mit einem Test gegen Live/Import zaehlen.

## Empfohlene Patch-Reihenfolge

Bereits erledigt:

1. `GET /v1/mcp` NameError behoben und Test ergaenzt.
2. MCP-Allowlist in `tools/list` und `tools/call` erzwungen; externe Runtime-Tests ergaenzt.
3. Group-Chat-State-Dir ueber `TRISTAR_GROUP_CHAT_DIR`/`TRISTAR_STATE_DIR` konfigurierbar und mit Fallback versehen.
4. `tools/upsonic_triforce.py` F821 behoben.

Weiter empfohlene Reihenfolge:

1. Default-Modell-Policy zentralisieren und alle Defaults auf `ollama/gemma4:12b` ziehen.
2. Client-Chat Routing fuer Registered/Analyze korrigieren.
3. Service-Units konsolidieren und README/Makefile/AGENTS synchronisieren.
4. Repo-Hygiene-PR fuer bereits getrackte Runtime-/Build-Artefakte.
5. Dependency-Baseline locken oder Requirements aktualisieren.
6. Ruff/CI-Gates stufenweise einfuehren.

## Validierungsprotokoll

Ausgefuehrte Checks:

```text
python3 -V
=> Python 3.14.4

.venv/bin/python -m compileall app tests scripts tools -q
=> OK

.venv/bin/pytest -q
=> 178 passed

.venv/bin/python -m pytest tests/test_security_findings.py tests/test_mcp_security_hardening.py tests/test_group_chat_hardening_unittest.py tests/test_mcp_swarm_runtime_contract_unittest.py -q
=> 67 passed

.venv/bin/python -m pytest tests/test_unified_registry_unittest.py tests/test_structured_admin_handlers_unittest.py tests/test_mcp_tool_name_normalization_unittest.py tests/test_mcp_write_fallback_unittest.py -q
=> 15 passed

.venv/bin/python -m pip check
=> No broken requirements found

.venv/bin/ruff check tools/upsonic_triforce.py --select F821
=> All checks passed!

.venv/bin/ruff check app tests scripts tools
=> 741 errors vor dem F821-Fix; global weiterhin nicht als Gate geeignet
```

Live-Smoke-Checks aus dem Audit vor dem lokalen Code-Patch:

```text
Hinweis: Der produktive systemd-Dienst wurde fuer diesen Patch nicht neu gestartet.

systemctl is-active triforce.service
=> active

curl -sS http://127.0.0.1:9000/health
=> {"ok":true,"status":"ok"}

curl -sS http://127.0.0.1:9000/v1/.well-known/mcp
=> OK, streamable-http metadata

curl -sS http://127.0.0.1:9000/v1/mcp
=> 500, NameError: params vor lokalem Patch; lokaler Regressionstest ist gruen

curl -sS -X POST http://127.0.0.1:9000/v1/mcp ... tools/list
=> OK, count 145

curl -sS http://127.0.0.1:9000/v1/mcp/status
=> OK, 205 methods, model_count 743

curl -sS http://127.0.0.1:9000/v1/models
=> OK, total 742, excluded 1
```
