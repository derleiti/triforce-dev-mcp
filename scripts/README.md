# 🔱 TriForce Admin Scripts

> Current safety rule (TriForce **2.86.4**): operational scripts can be destructive. Back up affected configuration/data first, prefer narrowly scoped commands, and verify current paths/services before using historical examples. Do not run broad cleanup against production merely because a script exists here.

Administration und Automatisierung für TriForce Docker Infrastructure.

## 📁 Struktur

```
scripts/
├── docker/                    # Docker Container Scripts
│   ├── stack-control.sh       # Start/Stop/Restart aller Stacks
│   ├── health-check.sh        # System Health Check
│   ├── wordpress/             # WordPress-spezifische Scripts
│   ├── flarum/                # Flarum Forum Scripts
│   ├── mailserver/            # Mailserver Scripts
│   ├── repository/            # APT Repository Scripts (30+)
│   └── searxng/               # SearXNG Scripts
├── backup/                    # Backup & Recovery
│   ├── backup-all.sh          # Komplett-Backup (DB + Config)
│   ├── restart-all-stacks.sh  # Alle Container neu starten
│   ├── generate-secrets.sh    # Secrets generieren
│   └── generate-configs.sh    # Configs generieren
├── maintenance/               # Wartung & Logs
│   ├── daily-cleanup.sh       # Tägliche Bereinigung (→ /etc/cron.daily/)
│   ├── rotate-logs.sh         # Log-Rotation
│   └── crontab.template       # Crontab Vorlage
├── system/                    # System-Administration
│   ├── systemoptimizer.sh     # System-Optimierung
│   ├── dep-heal.sh            # Dependency Healing
│   └── cf-update-certs.sh     # Cloudflare Zertifikate
└── wrappers/                  # CLI Wrapper Scripts
```

## 🚀 Schnellstart

### Alle Container verwalten
```bash
./scripts/docker/stack-control.sh start all      # Alle starten
./scripts/docker/stack-control.sh stop all       # Alle stoppen
./scripts/docker/stack-control.sh restart all    # Alle neustarten
./scripts/docker/stack-control.sh status all     # Status anzeigen
./scripts/docker/stack-control.sh logs wordpress # Logs eines Stacks
```

### Health Check
```bash
./scripts/docker/health-check.sh
```

### Backup
```bash
./scripts/backup/backup-all.sh
```

## 🧹 Maintenance

### Daily Cleanup installieren
```bash
sudo cp scripts/maintenance/daily-cleanup.sh /etc/cron.daily/triforce-clean
sudo chmod +x /etc/cron.daily/triforce-clean
```

### Crontab einrichten
```bash
crontab -e
# Inhalt von scripts/maintenance/crontab.template einfügen
```

### Was wird aufgeräumt?
| Task | Frequenz | Aktion |
|------|----------|--------|
| Docker Prune | Täglich | Ungenutzte Images/Volumes löschen |
| Journalctl | Täglich | Logs auf 200MB begrenzen |
| Triforce Logs | Täglich | Logs älter 7 Tage löschen |
| apt-mirror clean | Täglich | Alte Paketversionen löschen |

## 📦 Repository Scripts

| Script | Beschreibung |
|--------|--------------|
| `update-mirror.sh` | Haupt-Mirror-Pipeline (Download → Sign → Index) |
| `sign-repos.sh` | Alle Repos mit GPG signieren |
| `maintain.sh` | Wartungsaufgaben |
| `repo-health.sh` | Repository Health Check |
| `mirror-speedtest.sh` | Mirror Geschwindigkeit testen |

## 🔐 Sicherheit

- Alle Scripts nutzen `$TRIFORCE_DIR` (default: `~/triforce`)
- `.env` wird aus `config/triforce.env` geladen
- Backups werden 7 Tage aufbewahrt
- GPG-Keys sind in `docker/repository/etc/gnupg/`

## 📊 Statistik

| Kategorie | Anzahl |
|-----------|--------|
| Docker Scripts | 36 |
| Backup Scripts | 4 |
| Maintenance | 3 |
| System Scripts | 3 |
| **Gesamt** | **65+** |

---
🐻 *Brumo: "Scripts sind wie Honig - gut organisiert schmeckt's besser."*
