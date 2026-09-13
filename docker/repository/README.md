# 🗂️ AILinux Repository

> Current repository note: this is the AILinux package-repository stack used by the wider AILinux family. Treat mirror size, distributions and signing configuration as deployment-specific; verify current repository configuration before running destructive mirror maintenance.

**Status: Early Alpha**

Lokales APT Repository für AILinux Pakete.

## Funktionen

- **Custom Packages**: Eigene .deb Pakete (ailinux-project, nova-ai, etc.)
- **APT Mirror** (optional): Ubuntu/Debian Mirror für Offline-Installationen

## Verwendung

```bash
# Repository starten
docker compose up -d

# Mit APT Mirror (braucht ~500GB+ Speicher!)
docker compose --profile mirror up -d
```

## Repository hinzufügen (Client)

```bash
curl -fsSL "https://repo.ailinux.me/mirror/add-ailinux-repo.sh" | sudo bash
```

## Paket erstellen

```bash
cd ~/ailinux-project/packaging
./build-deb.sh
# Dann nach repository/data/pool/ kopieren
reprepro -b ./data includedeb noble ../packaging/*.deb
```
