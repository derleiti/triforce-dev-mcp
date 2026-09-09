# Migration auf TriForce 2.85 Beta 1

## Zielmodell

TriForce 2.85 Beta 1 trennt Programmcode, Konfiguration und Laufzeitdaten:

- Programm: `/opt/triforce`
- zentrale Konfiguration: `/etc/triforce/triforce.env`
- Zustand: `/var/lib/triforce`
- Logs: `/var/log/triforce`
- systemd Unit: `triforce.service`

Produktiver Code soll nicht mehr direkt auf Entwicklerpfade wie `/home/zombie/triforce/.env` zugreifen.

## Bestehende Konfiguration übernehmen

Vor einer Migration aus einem Checkout-basierten Betrieb die bestehende produktive Konfiguration sichern und relevante Werte kontrolliert in `/etc/triforce/triforce.env` übernehmen. Secrets nicht in Shell-History, Git oder Paketdateien kopieren. Das Control Center kann die root-geschützte Datei über den begrenzten PolicyKit-Helper redigiert lesen und atomar aktualisieren.

Eine vorhandene `/etc/triforce/triforce.env` wird durch `config-init` nicht überschrieben. Änderungen erzeugen eine einzelne `.last-good`-Sicherung.

## Dienstmigration

Der Paketdienst wird bei Fresh Install nicht automatisch aktiviert oder gestartet. Nach Prüfung der Konfiguration:

```bash
triforce-control check diagnose
sudo systemctl enable triforce.service
sudo systemctl start triforce.service
```

Start/Stop und Enable/Disable bleiben absichtlich getrennte Aktionen.

## Upgrade-Verhalten

Beim Paketupgrade bleiben `/etc/triforce` und `/var/lib/triforce` erhalten. Ein getesteter synthetischer Upgrade-Pfad von `2.85.0~beta0` auf `2.85.0~beta1` bewahrte Konfiguration, lokal generiertes MCP-Secret, State und den Enable-Zustand.

## Rollback

Für eine einzelne fehlerhafte Config-Änderung kann die `.last-good`-Konfiguration über den begrenzten Helper wiederhergestellt werden. Für einen Paketrollback vorher die gewünschte ältere `.deb`-Version sowie die aktuelle Betreiber-Konfiguration sichern. State-/Config-Verzeichnisse werden bei `remove`/`purge` absichtlich nicht automatisch gelöscht.
