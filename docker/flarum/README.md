# Flarum Forum Docker Setup

Dieses Setup stellt ein vollständiges Flarum-Forum mit Apache Reverse Proxy, MariaDB-Datenbank und automatischen Backups bereit.

## Architektur

Das Setup besteht aus folgenden Services:

- **Apache (httpd:2.4-alpine)** - Reverse Proxy mit SSL/TLS auf Ports 8080/8443
- **Flarum (mondedie/flarum:1.8.9)** - Forum-Anwendung auf Port 8000
- **MariaDB 11** - Datenbank-Backend mit persistentem Volume
- **Backup Service** - Automatische tägliche Datenbank-Backups

### Integration mit WordPress Apache-Konfiguration

Der Apache-Container nutzt die gleiche Konfiguration wie das WordPress-Setup aus `/var/lib/ailinux/wordpress/apache`:

- `httpd.conf` - Haupt-Konfiguration
- `cloudflare-allowlist.conf` - Cloudflare IP-Allowlist & Real-IP Logging
- `vhosts/vhost-forum.ailinux.me.conf` - Virtual Host für das Forum
- `snippets/ssl-params.conf` - SSL/TLS Parameter
- Let's Encrypt Zertifikate aus `/etc/letsencrypt`

## Installation

### 1. Environment-Datei erstellen

```bash
cp .env.example .env
```

### 2. Passwörter und Konfiguration anpassen

Bearbeiten Sie die `.env`-Datei und ändern Sie:

```bash
# Sichere Passwörter setzen
DB_PASS=IhrSicheresPasswort123
DB_ROOT_PASSWORD=IhrSicheresRootPasswort123
FLARUM_ADMIN_PASSWORD=IhrSicheresAdminPasswort123

# Admin-Email anpassen
FLARUM_ADMIN_EMAIL=ihre-email@ailinux.me

# Optional: Forum-Titel anpassen
FLARUM_TITLE=Ihr Forum-Titel
```

### 3. Container starten

```bash
docker compose up -d
```

Beim ersten Start:
1. MariaDB-Datenbank wird initialisiert
2. Flarum wird installiert und konfiguriert
3. Admin-Account wird automatisch angelegt
4. Backup-Service startet (Backups um 03:00 UTC)

### 4. Forum aufrufen

Das Forum ist nach dem Start verfügbar unter:

- Lokal: `http://localhost:8080` oder `https://localhost:8443`
- Produktiv: `https://forum.ailinux.me` (nach Cloudflare-Konfiguration)

**Hinweis**: Die Apache-Ports sind auf 8080/8443 gemappt, um Konflikte mit dem WordPress-Setup (80/443) zu vermeiden.

## Verwaltung

### Container-Status prüfen

```bash
docker compose ps
docker compose logs -f flarum
```

### Container neustarten

```bash
docker compose restart flarum
```

### Container stoppen

```bash
docker compose down
```

**ACHTUNG**: Folgendes löscht auch die Datenbank!

```bash
docker compose down -v
```

### Updates installieren

```bash
docker compose pull
docker compose up -d
```

### Flarum-CLI verwenden

```bash
docker compose exec flarum php flarum <command>

# Beispiele:
docker compose exec flarum php flarum info
docker compose exec flarum php flarum cache:clear
docker compose exec flarum php flarum migrate
```

## Backup & Restore

### Automatische Backups

Der Backup-Service erstellt täglich um 03:00 UTC automatisch Backups:

- Speicherort: `./backups/`
- Format: `flarum-YYYYMMDD_HHMMSS.sql.gz`
- Retention: 30 Tage (ältere Backups werden automatisch gelöscht)

### Manuelles Backup

```bash
docker compose exec flarum_db mariadb-dump -u root -p${DB_ROOT_PASSWORD} flarum | gzip > backups/manual-$(date +%Y%m%d_%H%M%S).sql.gz
```

### Restore aus Backup

```bash
# Container stoppen
docker compose stop flarum

# Backup wiederherstellen
gunzip < backups/flarum-YYYYMMDD_HHMMSS.sql.gz | docker compose exec -T flarum_db mariadb -u root -p${DB_ROOT_PASSWORD} flarum

# Container neu starten
docker compose start flarum
```

## Verzeichnisstruktur

```
falrum/
├── docker-compose.yml          # Hauptkonfiguration
├── .env                        # Umgebungsvariablen (nicht committen!)
├── .env.example                # Beispiel-Konfiguration
├── README.md                   # Diese Datei
├── mysql/
│   └── custom.cnf             # MariaDB-Konfiguration
├── backups/                   # Datenbank-Backups
└── flarum/                    # Flarum-Daten
    ├── assets/                # Öffentliche Assets
    ├── extensions/            # Flarum-Erweiterungen
    ├── storage/               # Uploads & Cache
    └── nginx/                 # Nginx-Konfiguration (optional)
```

## Erweiterungen installieren

Flarum-Erweiterungen können über Composer installiert werden:

```bash
docker compose exec flarum composer require <vendor>/<extension>
docker compose exec flarum php flarum migrate
docker compose exec flarum php flarum cache:clear
```

Beispiele:

```bash
# Deutsch-Sprachpaket
docker compose exec flarum composer require flarum-lang/german

# BBCode-Unterstützung
docker compose exec flarum composer require flarum/bbcode

# Markdown-Toolbar
docker compose exec flarum composer require flarum/markdown
```

## Fehlerbehebung

### Health Checks fehlgeschlagen

```bash
# Flarum-Health prüfen
docker compose exec flarum curl -f http://localhost:8000/api/health

# Logs prüfen
docker compose logs --tail=100 flarum
```

### Datenbank-Verbindungsfehler

```bash
# MariaDB-Health prüfen
docker compose exec flarum_db mariadb-admin ping -u root -p${DB_ROOT_PASSWORD}

# In Datenbank einloggen
docker compose exec flarum_db mariadb -u root -p${DB_ROOT_PASSWORD}
```

### Berechtigungsprobleme

```bash
# Flarum-Container als root ausführen
docker compose exec -u root flarum sh

# Berechtigungen korrigieren
chown -R www-data:www-data /flarum/app/storage
chown -R www-data:www-data /flarum/app/public/assets
```

## Produktiv-Deployment

### 1. Apache-Ports anpassen

Für Produktiv-Betrieb die Ports in `docker-compose.yml` auf 80/443 ändern:

```yaml
apache:
  ports:
    - "80:80"
    - "443:443"
```

### 2. Cloudflare DNS konfigurieren

DNS-Eintrag für `forum.ailinux.me` erstellen:

- Typ: A-Record
- Name: forum
- Wert: Server-IP
- Proxy: Aktiviert (orange Cloud)

### 3. SSL-Zertifikat prüfen

Sicherstellen, dass Let's Encrypt Zertifikate vorhanden sind:

```bash
ls -la /etc/letsencrypt/live/ailinux.me/
```

### 4. Firewall-Regeln

```bash
# UFW
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# iptables
sudo iptables -A INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 443 -j ACCEPT
```

## Sicherheitshinweise

- `.env`-Datei **niemals** committen (ist in `.gitignore`)
- Starke Passwörter für Datenbank und Admin verwenden
- Regelmäßig Updates installieren
- Backups an sicheren Ort kopieren (nicht nur lokal)
- Cloudflare-Allowlist ist aktiv (nur Cloudflare-IPs erlaubt)
- SSL/TLS ist vorkonfiguriert (TLS 1.2+)

## Support & Dokumentation

- Flarum-Dokumentation: https://docs.flarum.org
- mondedie/flarum Image: https://github.com/mondediefr/docker-flarum
- Flarum-Community: https://discuss.flarum.org

## Lizenz

Dieses Setup ist frei nutzbar. Flarum selbst steht unter der MIT-Lizenz.

## AILinux production policy

AILinux keeps the Flarum application image pinned in `config/triforce.env` and MariaDB on an explicit minor series. Normal lifecycle operations use `scripts/docker/stack-control.sh`; the `backup` profile is separate from the always-on runtime. Secrets belong in the canonical ignored environment file, never in this repository.
