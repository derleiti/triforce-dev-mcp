# TriForce MCP Tool Audit — 2026-09-09

## Ziel

Die MCP-Oberfläche wurde auf das Prinzip **eine kanonische Fähigkeit = ein advertised Tool** reduziert. Historische Aliase und kompatible Handler dürfen intern weiter existieren, werden Modellen aber nicht mehr automatisch als eigene Fähigkeiten präsentiert.

Das reduziert Tool-Context, falsche Tool-Auswahl und Registry-Drift, ohne bestehende Legacy-Calls unnötig zu brechen.

## Ausgangslage

Vor dem Audit lieferte der produktive `tools/list`-Pfad **149 advertised Tools**. Der V5-Registry-Bestand enthielt 101 rohe Schemas und 110 Alias-Mappings; zusätzliche WordPress-, Browser-, Redis-, Performance- und n8n-Schemas wurden im Route-Layer noch einmal ergänzt.

Eine statische Auflösungsprüfung gegen Route-Dispatcher, `MCP_HANDLERS` und V4-Handler fand **13 advertised Tools ohne erreichbaren Handler**:

- `hive_compress`
- `hive_recall`
- `hive_stats`
- `agent_chat_list`
- `agent_chat_read`
- `agent_chat_stream`
- `agent_chat_summary`
- `agent_chat_cleanup`
- `model_performance`
- `model_recommend`
- `redis_cleanup`
- `router_dashboard`
- `router_select`

Die `hive_*`-Schemas hatten keine eigentliche Handler-Implementierung. Bei den übrigen Fällen existierte teilweise Implementierungscode, er war aber nicht in der aktiven `tools/call`-Dispatch-Kette registriert. Ein advertised Tool ohne erreichbaren Handler gilt unabhängig davon als defekt.

## Konsolidierung

Der Unified Registry besitzt jetzt zwei explizite Oberflächen:

- **Core/default:** 39 Tools für allgemeine Agentenarbeit.
- **Canonical full (`inventory=all`):** 79 Tools für die komplette unterstützte Oberfläche.

Spezialisierte Fähigkeiten werden bei Bedarf über Inventories geladen, z. B. `memory`, `filesystem`, `group_chat`, `forum`, `wordpress`, `browser` oder `integration`.

### Beispiele entfernte Doppelungen aus Discovery

| Nicht mehr advertised | Kanonische Alternative | Grund |
|---|---|---|
| `code_read` | `file_ops` | `file_ops` deckt Lesen und weitere sichere Dateioperationen ab |
| `code_patch` | `code_edit` | eine kanonische Code-Schreiboberfläche |
| `logs_errors`, `logs_stats` | `log_viewer` | Filter/Quellen in einem Tool statt Spezialvarianten |
| `mcp_telemetry` | `mcp_analytics` | Analytics ist die umfassendere Diagnoseoberfläche |
| `git_ops` | `git` | gleiche Git-Funktionsklasse |
| `image_search` | `search` | Unified Search unterstützt auch Image-Intent |
| `ollama_run` | `chat` | generische Modellinferenz statt separater Lauf-API |
| `ollama_list` | `models` | zentrale Modellübersicht |
| `flarum_discussion` | `flarum_discussion_get` | beide routen auf dieselbe Discussion-Lesefunktion |
| `group_chat_status` | `group_chat_list` / `group_chat_read` | Status ist bereits über Session-Liste und Read-Kontext verfügbar |
| `wp_publish_post` | `wp_create_draft` + `wp_update_post` | kontrollierter Create/Update-Lifecycle statt zweitem Create-Pfad |
| `n8n_workflow_create` | `n8n_mcp_call` | generischer n8n-MCP-Zugriff ist umfassender |

Die alten Handler werden in diesem Schritt nicht pauschal gelöscht. Sie bleiben als Kompatibilitätsschicht für bekannte Alt-Clients bestehen, verbrauchen aber keinen Tool-Context mehr.

## Handler-Verifikation

Nach der Konsolidierung wurde die komplette advertised Oberfläche erneut gegen alle aktiven Dispatch-Wege geprüft:

- Core: **39 advertised, 0 unresolved**
- Full canonical: **79 advertised, 0 unresolved**

Damit gilt erstmals für die normale Discovery: **jedes angebotene Tool besitzt einen erreichbaren Handler**.

## Weiterer Code-Review-Befund: Versionsdrift

Mehrere MCP-Handshake-/Metadata-Pfade meldeten noch hartcodiert Backend `2.80`, obwohl `app/config.py` bereits `VERSION = "2.81"` definiert. Die Runtime-Metadaten wurden auf diese zentrale Versionsquelle umgestellt; `app.mcp.__version__` verwendet nun ebenfalls die zentrale TriForce-Version.

## Designregel ab jetzt

Neue MCP-Fähigkeiten sollen nur advertised werden, wenn:

1. ein aktiver Handler im realen `tools/call`-Pfad erreichbar ist,
2. keine bereits advertised kanonische Fähigkeit denselben Use Case ausreichend abdeckt,
3. ein klarer Inventory-Bereich existiert,
4. Default/Core nur erweitert wird, wenn die Fähigkeit für allgemeine Agentenarbeit wirklich notwendig ist.

Regressionstests begrenzen Core auf maximal 40 und die vollständige kanonische Oberfläche auf maximal 80 Tools und prüfen bekannte tote bzw. doppelte Toolnamen gegen erneute Veröffentlichung.

## Dispatcher-/Discovery-Unifizierung

Im zweiten Review-Schritt wurde auch der verbliebene Parallelpfad in `app/services/mcp_service.py` entfernt. Das Modul enthielt zuvor eine eigene vollständige `handle_tools_list()`-Schema-Liste und einen separaten `handle_tools_call()`-Dispatcher. Dadurch konnten Route und Service unabhängig voneinander altern und unterschiedliche Tools anbieten oder unterschiedlich routen.

Die alten öffentlichen Python-Symbole bleiben für bestehende Imports erhalten, sind jetzt aber nur noch dünne Compatibility-Delegates:

```text
app.services.mcp_service.handle_initialize ─┐
app.services.mcp_service.handle_tools_list  ├─> app.routes.mcp (canonical)
app.services.mcp_service.handle_tools_call  ┘
```

Dadurch wurden **494 Zeilen** duplizierte Discovery-/Dispatch-Logik entfernt. Anschließend konnten **44 unbenutzte Imports** aus `mcp_service.py` entfernt werden. `ruff` meldet für `F401/F821` danach keine Findings.

Regressionstests prüfen jetzt zusätzlich, dass Route und Service für `core`, `all`, `memory` und `filesystem` exakt dieselbe Discovery-Antwort liefern und dass Tool-Calls über den Service-Einstieg dasselbe Resultat wie der kanonische Route-Dispatcher erzeugen.
