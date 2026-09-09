# TriForce 2.85 Beta 1 – Build

Getesteter Buildhost: Ubuntu 26.04.1 LTS amd64, Python 3.14.

## Backend-Runtime

Die Backend-Abhängigkeiten werden in einer separaten venv aufgelöst. Für den geprüften Beta-Build ist die vollständige Auflösung in `requirements-lock-2.85-beta1.txt` festgehalten. Das Debian-Paket liefert diese isolierte Runtime mit; `postinst` führt weder Online-`pip` noch Änderungen am System-Python aus.

## Control Center

Geprüfte Buildversionen: PyQt6 6.11.0 und Nuitka 4.2.1. Zuerst wird ein Standalone-Bundle gebaut. Nuitka empfiehlt Standalone vor Onefile, weil fehlende Dateien dort leichter erkennbar sind. PyQt ist GPLv3/kommerziell dual lizenziert; das TriForce-Repository ist AGPLv3. Die GPL-PyQt-Wheels bringen die korrespondierenden LGPL-Qt-Bibliotheken mit.

Beispiel:

```bash
python3.14 -m venv /tmp/triforce-gui-build
/tmp/triforce-gui-build/bin/pip install PyQt6==6.11.0 Nuitka==4.2.1 ordered-set zstandard patchelf
/tmp/triforce-gui-build/bin/python -m nuitka --mode=standalone --enable-plugin=pyqt6 \
  --output-dir=/tmp/triforce-2.85-nuitka --output-filename=triforce-control-center \
  control_center/main.py
```

Das Paket-Skript erwartet die gebaute Backend-Runtime und das Standalone-GUI-Bundle und erzeugt zwei amd64-Pakete plus Manifest und SHA256SUMS:

```bash
BACKEND_RUNTIME=/tmp/triforce-2.85-backend-runtime \
GUI_BUNDLE=/tmp/triforce-2.85-nuitka/main.dist \
./scripts/release/build-triforce-2.85-beta1.sh
```

Der Staging-Code kopiert Quellen explizit und nimmt keine `.env`, `auth/`, Datenbanken, Logs, Backups, `.git` oder Docker-Volumes mit. Ein Secret-Pattern-Scan läuft vor `dpkg-deb`.
