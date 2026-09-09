# TriForce 2.85 Beta 1 – Arbeits- und Wiederaufnahmebericht

Stand: 2026-09-09
Branch: `feat/triforce-2.85-beta1`
Ausgangscommit: `992c8bac2bba8c873987a62747c31336acdc9c35`
Arbeits-Worktree: `/home/zombie/triforce-2.85-beta1`
Produktions-Checkout: `/home/zombie/triforce` (nicht verändert)

## Verifizierter Ausgangszustand

- Host: `ailinux`.
- Produktionsdienst: `triforce.service`, aktiv und enabled, Benutzer/Gruppe `zombie`.
- Produktions-ExecStart: `/home/zombie/triforce/scripts/start-triforce.sh`.
- Produktions-Unit lädt `/home/zombie/triforce/config/triforce.env`.
- Alter Startwrapper sourced dieselbe Datei zusätzlich, synchronisierte MCP-OAuth nochmals aus `.env` und startete hart auf `0.0.0.0:9000`.
- `app/config.py` lud separat `.env` via Pydantic.
- `/v1/settings` persistierte Änderungen nicht, sondern schrieb nur `os.environ`.
- TriStar `SettingsController` besitzt zusätzlich JSON/Secrets-Store sowie einen hartcodierten `/home/zombie/triforce/.env`-Writer; Secrets werden dort nur XOR/Base64-obfuskiert.
- Alter Release-Builder kopiert `config/` vollständig und war auf `4.8.0-beta`/`Architecture: all` voreingestellt.
- `VERSION` stand auf `2.86.0`, `app/config.py` auf `2.81`: Versionsquelle war inkonsistent.
- Produktions-venv: Python 3.14.4, Pydantic 2.13.4, pydantic-settings 2.15.0, python-dotenv 1.2.2, pytest 9.1.1. PyQt6/Nuitka sind dort nicht installiert.
- Host-Buildtools: dpkg 1.23.7, GCC 15.2.0, Qt 6.11.1, qemu-system-x86_64 vorhanden.

## Architektur für Beta 1

1. `app/settings_store.py` ist der gemeinsame, offline nutzbare Datei-/Schema-Unterbau. Kein `app/config/`-Package, damit `app/config.py` nicht überschattet wird.
2. Priorität: Prozess-Environment > ausgewählte kanonische Datei > Pydantic-Defaults.
3. Produktionsziel: `/etc/triforce/triforce.env`; Entwicklung/Bestandsinstallation kann explizit `TRIFORCE_CONFIG_FILE` setzen. Repo-Fallback: `config/triforce.env`.
4. Dotenv wird ausschließlich als Daten geparst; kein `source`/`eval` im Backend-Startpfad.
5. Speichern: vollständige Pydantic-Validierung, Digest-Konflikterkennung, atomarer Replace, Moduserhalt, genau eine `.last-good`-Kopie.
6. Secrets werden über eine explizite Allowlist klassifiziert und bei Diagnose/Anzeige redigiert.
7. Server-Host/-Port/-Keepalive sind Schemafelder und werden vom Startwrapper tatsächlich verwendet.
8. systemd bleibt Prozessmanager; GUI startet keinen langlebigen Backend-Kindprozess.

## Phase 1 – gemeinsamer Settings-Unterbau

Erledigt:
- `app/settings_store.py` angelegt.
- `app/config.py` von eigener `.env`-Ladung gelöst.
- `TRIFORCE_BIND_HOST`, `TRIFORCE_API_PORT`, `TRIFORCE_KEEPALIVE` ins Schema aufgenommen.
- Startwrapper entfernt Shell-`source` und OAuth-Sonderüberschreibung; Host/Port kommen aus Environment bzw. kanonischem Parser.
- Zielversion im Branch auf `2.85.0~beta1` / Anzeige `2.85 Beta 1` gestellt.

Getestet:
- `tests/test_settings_store.py`: 6 Tests bestanden.
- `python -m compileall app -q`: bestanden.
- `bash -n scripts/start-triforce.sh`: bestanden.
- `git diff --check`: bestanden.

## Noch offen

- `/v1/settings` auf persistente gemeinsame Schicht umstellen und vollständiges Inventar/Origins bereitstellen.
- TriStar-Settings-Kompatibilitätsweg entschärfen/migrieren.
- Setup-Aufgabenkatalog + CLI.
- begrenzter Admin-Helper + PolicyKit.
- PyQt6 Control Center.
- Nuitka-Build in separater Build-venv.
- neue Debian-Paketierung ohne Secrets/Produktionsdaten.
- vollständige Testsuite, Paketinstall/Upgrade/Remove/Purge und systemd-VM-E2E.

## Schutz der Produktion

Bis zu diesem Stand wurden weder `/home/zombie/triforce`, `/etc/systemd/system/triforce.service` noch der laufende Produktionsdienst verändert oder neu gestartet.

## Phase 2 – Setup, Admin-Helper und Control Center

Erledigt:
- Gemeinsamer deklarativer Setup-Taskkatalog (`app/setup_tasks.py`) für CLI und GUI.
- Headless-CLI `bin/triforce-control` mit Task-/Plan-/Check-/Run-/Settings-Kommandos.
- Eng begrenzter Admin-Helper mit festen Aktionen und festem `triforce.service`; keine freie Unit-, Script- oder Shell-Weiterleitung.
- Paket-Servicevorlage für eigenen Benutzer `triforce`, `/etc/triforce`, `/var/lib/triforce`, journald und getrennte Enable/Start-Aktionen.
- PyQt6 Control Center mit Übersicht, vollständiger Schema-/Experteneinstellungstabelle, Suche/Filter, Secret-Maskierung, Dienstbedienung, getrenntem Server-/Desktop-Autostart, Setup-Aufgaben und redigierter Diagnose.
- Desktop-Datei und skalierbares SVG-Icon.

Getestet:
- 13 fokussierte Settings/Setup/Helper-Tests bestanden.
- Python-Syntax/compileall und `git diff --check` bestanden.
- PyQt6 6.11.0 / Qt 6.11.x offscreen aus bereinigter Umgebung: 5 Seiten, 151 Settings-Zeilen.
- Nuitka 4.2.1 Standalone-Build auf Ubuntu 26.04.1 amd64 erfolgreich.
- Kompiliertes Bundle außerhalb des Quellverzeichnisses mit `QT_QPA_PLATFORM=offscreen`: `GUI_SMOKE_OK pages=5 settings_rows=151`.
- Gerenderter Smoke-Screenshot: 1180x760 PNG.

Build-Hinweis:
- Temporäre Build-venv: `/tmp/triforce-2.85-gui-build-venv`.
- Standalone-Bundle: `/tmp/triforce-2.85-nuitka/main.dist` (~170 MB).
- Nuitka weist bei 4.2.1 selbst darauf hin, dass PyQt6-Unterstützung nicht perfekt ist; der Bundle-Smoke ist bestanden, diese Upstream-Einschränkung bleibt dokumentiert.
- Für die C-Kompilierung wurden auf dem Buildhost ausschließlich `python3.14-dev` und `libpython3.14-dev` als OS-Buildheader nachinstalliert. Kein System-Python-pip und kein TriForce-Produktionsdienst wurde verändert.

## Phase 3 – Paket-/Runtime-Hardening und MCP-Auth

Erledigt:
- Laufzeitpfade für paketierte Installation auf kanonische `TRIFORCE_*_DIR`-Pfade konsolidiert; Programmdateien bleiben unter `/opt/triforce`, veränderbare Daten unter `/var/lib/triforce`, Logs unter `/var/log/triforce`.
- Legacy `/var/tristar` wird bei neuen Installationen nur dann als Kompatibilitäts-Symlink nach `/var/lib/triforce/tristar` angelegt, wenn dort nichts Bestehendes liegt.
- Federation ist ohne `FEDERATION_SECRET` optional und fail-closed; bei Fresh-Install wird der Federation-Manager ohne Secret gar nicht gestartet.
- Separater MCP-WebSocket-Listener ist bei Fresh-Install standardmäßig deaktiviert.
- `/v1/settings` persistiert nun über `app.settings_store`, mit Digest-Konflikterkennung und Admin-Schutz; keine Runtime-only `os.environ`-Mutation mehr.
- Neuer `app.server_launcher`: dotenv wird einmal als Daten geparst/validiert und als Prozessumgebung an Uvicorn weitergereicht. Damit sehen direkte `os.getenv()`-Altverbraucher denselben kanonischen Ladeweg ohne Shell-`source`/`eval`.
- MCP-Auth verlangt jetzt standardmäßig auch auf Loopback Credentials. Ein lokaler Bypass existiert nur noch über `MCP_ALLOW_UNAUTHENTICATED_LOCAL=true`, ausschließlich für echte Loopback-Aufrufe ohne Forwarding-Header.
- Fresh-Install übernimmt keine Secret-Platzhalter aus `triforce.env.example`; MCP/JWT/Admin-Secrets werden lokal kryptografisch erzeugt und nicht ausgegeben.
- Privilegierter Helper verwendet die isolierte Paket-Runtime statt System-Python für Settings-Operationen.

Systemd-Container-E2E:
- Fresh install: Pakete installiert, Dienst danach `inactive` und `disabled`, Redis nicht automatisch installiert.
- Dienststart: `active`, `/health` HTTP 200.
- Portänderung über atomaren Config-Helper: 9100 -> 19120; nach kontrolliertem Neustart 19120 bereit, 9100 geschlossen; Boot-Autostart blieb `disabled`.
- MCP kanonisch: 39 Core / 79 vollständige Tools, keine Duplikate; read-only `status`-Smoke bestanden.
- MCP unauthentifiziert nach Hardening: HTTP 401; authentifizierter MCP-Smoke bestanden.

Tests:
- Gebündelter isolierter Regressionlauf: 68 passed.
- `compileall`, `py_compile`, `bash -n`, `git diff --check` bestanden.

## Finale Beta-1-Abnahme (2026-09-09)

Der aktuelle Kandidat wurde nach den letzten Settings-/Setup-Korrekturen vollständig neu gebaut und erneut geprüft.

### Erledigt und getestet

- Zentrale Konfiguration: Prozess-Environment → `/etc/triforce/triforce.env` → Defaults; keine produktiven Python-Fallbacks mehr auf `/home/zombie/triforce/.env`.
- TriStar-, Anthropic- und Flarum-Konfigurationszugriffe auf den zentralen Settings-Store umgestellt.
- Geschützter Raw-Editor: `config-read` liefert ausschließlich redigierte Secrets; `config-raw-update` stellt unveränderte Masken aus der root-lesbaren Originaldatei wieder her, validiert und schreibt atomar mit `.last-good`.
- Persistente Setup-Aufträge über den festen transienten systemd-Job `triforce-setup-job`; Abhängigkeiten werden vor Mutationen geprüft.
- Setup-Abbruch ist auf genau diesen festen Unit-Namen begrenzt und wurde gegen echtes systemd getestet.
- GUI-System-/Admin-Aktionen laufen über `QProcess` bzw. Worker statt blockierend im Qt-Eventloop.
- Vollständiger Testlauf: **253/253 passed**.
- Neu gebautes Nuitka-Standalone-GUI: Offscreen-Smoke **5 Seiten / 152 Settings-Zeilen**.
- Fresh install der finalen `.deb`: Dienst standardmäßig **disabled/inactive**, Config `root:triforce` `0640`, GUI als normaler Benutzer startbar.
- Setup-Runner auf Fresh Install: `runtime-init`, `config-init`, `service-install` jeweils `completed`.
- API Health: HTTP 200.
- MCP: ohne Auth HTTP 401; mit frisch generierten lokalen Credentials Tool-Discovery erfolgreich (**39 Core-Tools**).
- Sauberer systemd-Stop: `Result=success`.
- Synthetischer Upgrade-Test `2.85.0~beta0 → 2.85.0~beta1`: Config, lokales Secret, State und Enable-Zustand bleiben erhalten.
- `remove` und `purge`: Programmdateien/Enable-Link werden entfernt; Betreiber-Config unter `/etc/triforce` und State unter `/var/lib/triforce` bleiben absichtlich erhalten.

### Finale Artefakte

- `triforce-backend_2.85.0~beta1_amd64.deb`
  - SHA256 `0f336091d47483967bdebe783d4ddc2c1169725a826b8e6244b656d92d5df611`
- `triforce-control-center_2.85.0~beta1_amd64.deb`
  - SHA256 `2f786b9142e965234ade0f88de9dc10eadec9508c7354be774f839e80adc2378`

### Bewusste Grenzen von Beta 1

- Verifiziert auf **Ubuntu 26.04.1 LTS amd64 / Python 3.14**. Ubuntu 24.04/Noble ist mit diesem Build nicht verifiziert.
- Die Installations-/Upgrade-Abnahme lief in einem privilegierten Ubuntu-26.04-systemd-Container mit echtem systemd als PID 1; kein separater QEMU-VM-Test.
- Provider-Verbindungstests werden nicht automatisch beim Start ausgelöst. Das ist absichtlich fail-safe und vermeidet Kosten/Rate-Limits/Nebenwirkungen.
- Alte breit wirkende Installationsskripte mit globalen Paket-/npm-Eingriffen sind nicht als Root-Buttons im Control Center exponiert.


### Docker-Blueprint / MCP-Workflow

- Paketierter schlanker Blueprint unter `/opt/triforce/docker/blueprint` fuer Redis, WordPress/MariaDB, Flarum/MariaDB, SearXNG, n8n, Repository/nginx und docker-mailserver.
- Nur Compose, README, leere Strukturordner und Wartungsskripte; kein produktives WordPress-HTML, kein Repository-Mirror und keine Docker-Volumes.
- **59 strukturierte Docker-Settings** im zentralen TriForce-Settings-Store und Control Center.
- Fresh Install generiert getrennte lokale Secrets fuer WordPress-DB, Flarum-DB und SearXNG; geschuetzter Config-Read redigiert diese Werte.
- MCP-Tool `docker_stack` mit festen Actions `validate/status/up/down/restart/pull/logs` und festen Profiles `all/redis/wordpress/flarum/searxng/n8n/repository/mailserver`.
- Keine freie Compose-Datei, kein freier Shell-Befehl, kein automatisches Hinzufuegen des Dienstbenutzers zur Docker-Gruppe.
- Compose-Blueprint mit Docker Compose v5.5.1 fuer alle Profiles validiert.
- MCP-Discovery und Dispatch ueber echten HTTP-Endpunkt getestet.
- Vollstaendige Regression nach Docker-Integration: **261/261 passed**.


### Docker-Ein-Klick-Installation

- Setup-Seite enthaelt einen expliziten Button **Docker installieren**.
- Der Button startet den festen, ueberwachten Setup-Task `docker-install` ueber PolicyKit/systemd-run.
- Funktionale Vorpruefung: vorhandenes `docker --version`, `docker compose version` und `docker.service`; eine bereits funktionierende Installation wird nicht ersetzt.
- Falls erforderlich werden ausschliesslich die festen Pakete `docker.io` und `docker-compose-v2` mit `--no-install-recommends` installiert.
- Danach wird nur `docker.service` mit `systemctl enable --now` aktiviert/gestartet. Keine Compose-Profile werden automatisch gestartet.
- Der Dienstbenutzer `triforce` wird nicht zur Gruppe `docker` hinzugefuegt.
- Reale Fresh-Install-Pruefung im Ubuntu-26.04-systemd-Container: Docker 29.1.3 und Compose v2 2.40.3 installiert, Dienst enabled+active, Setup-Job `completed`, `triforce` weiterhin nur in eigener Gruppe.
- Regression nach Ein-Klick-Integration: **264/264 passed**.
