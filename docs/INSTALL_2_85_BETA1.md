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

## Setup-Aufträge und Abbruch

Das Control Center und `triforce-control` benutzen dieselben deklarativen Setup-Aufträge. Verändernde Aufträge werden über PolicyKit an den festen Helper delegiert und als transienter systemd-Job `triforce-setup-job` ausgeführt. Deklarierte Abhängigkeiten werden vor der Mutation geprüft.

CLI-Beispiele:

```bash
triforce-control tasks
triforce-control plan runtime-init
triforce-control run runtime-init
triforce-control job
triforce-control cancel
```

`cancel` kann ausschließlich den festen TriForce-Setup-Job stoppen; ein beliebiger Unit-Name kann nicht übergeben werden.

## Deinstallation

`remove` und `purge` löschen die installierten Programmdateien und deaktivieren die Unit. `/etc/triforce` und `/var/lib/triforce` werden aus Sicherheitsgründen nicht automatisch gelöscht, damit Betreiber-Konfiguration, lokale Secrets und Zustandsdaten nicht versehentlich verloren gehen.


## Optionaler Docker-Blueprint

Beta 1 liefert unter `/opt/triforce/docker/blueprint` eine schlanke
Compose-Grundstruktur fuer Redis, WordPress/MariaDB, Flarum/MariaDB, SearXNG,
n8n, Repository/nginx und docker-mailserver. Produktive WordPress-Dateien,
Repository-Mirror, Datenbanken, Maildaten und Volumes sind nicht Bestandteil
des Pakets.

Die Docker-Parameter werden in derselben `/etc/triforce/triforce.env` wie die
Backend-Einstellungen verwaltet und erscheinen im Control Center in der
Kategorie `Docker`. Secrets fuer WordPress/Flarum-Datenbanken und SearXNG werden
bei Fresh Install lokal generiert und im Editor redigiert.

Das MCP-Tool `docker_stack` bietet feste Aktionen `validate`, `status`, `up`,
`down`, `restart`, `pull` und `logs` fuer feste Profiles. Das Paket fuegt den
Dienstbenutzer **nicht** automatisch der Gruppe `docker` hinzu. Docker-Socket-
Zugriff ist praktisch Root-Zugriff und muss daher separat und bewusst vom
Betreiber freigegeben werden.


### Docker mit einem Klick installieren

Auf der Setup-Seite des Control Centers steht der Button **Docker installieren** zur Verfuegung. Nach PolicyKit-Autorisierung startet ein ueberwachter Setup-Job. Wenn Docker Engine und Compose v2 bereits funktionieren, wird die vorhandene Installation beibehalten. Andernfalls installiert TriForce ausschliesslich `docker.io` und `docker-compose-v2` aus den konfigurierten Debian/Ubuntu-Paketquellen und aktiviert `docker.service`.

Der Installer fuegt den Benutzer `triforce` absichtlich **nicht** zur Gruppe `docker` hinzu und startet keine Docker-Stacks. Docker-Socket-Rechte und das Starten einzelner Profiles bleiben eine separate Betreiberentscheidung.
