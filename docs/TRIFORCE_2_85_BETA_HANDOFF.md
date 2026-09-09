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
