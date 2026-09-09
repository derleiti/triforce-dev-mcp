# TriForce 2.85 Beta 1 – Installation und Betrieb

## Getestete Plattform

Diese Beta wurde nativ auf Ubuntu 26.04.1 LTS amd64 / Python 3.14 gebaut. Ubuntu 24.04/Noble ist **noch nicht als Binärziel verifiziert**, weil das Backend-Bundle derzeit Python 3.14 voraussetzt.

## Installation

```bash
sudo apt install ./triforce-backend_2.85.0~beta1_amd64.deb \
                 ./triforce-control-center_2.85.0~beta1_amd64.deb
```

Die Installation startet und aktiviert den Backend-Dienst absichtlich nicht. Initiale Konfiguration liegt unter `/etc/triforce/triforce.env`; Standardbindung ist lokal (`127.0.0.1:9100`).

```bash
triforce-control-center
# oder headless:
triforce-control tasks
triforce-control check diagnose
sudo systemctl start triforce.service
sudo systemctl enable triforce.service   # nur wenn Boot-Autostart gewünscht ist
```

Server-Start und Boot-Autostart sind unabhängig. Der Desktop-Autostart des Control Centers ist ebenfalls ein separater Schalter.

## Daten und Entfernen

Programmdateien: `/opt/triforce`. Konfiguration: `/etc/triforce`. Persistente Daten: `/var/lib/triforce`. Logs: journald und `/var/log/triforce` für Komponenten, die Dateilogging benötigen.

Bei `remove` wird ein laufender TriForce-Dienst gestoppt. Konfiguration und persistente Daten werden auch bei `purge` absichtlich **nicht automatisch gelöscht**. Damit kann eine Paketentfernung keine produktiven Daten still vernichten; eine spätere manuelle Datenlöschung bleibt eine bewusste Administratoraktion.
