# TriForce Docker Blueprint

Schlanke, paketierbare Docker-Grundstruktur fuer TriForce 2.85 Beta.
Enthalten: Compose/Config-Geruest, leere Mount-Verzeichnisse und Wartungsskripte.
Nicht enthalten: produktives WordPress-HTML, Repository-Mirror, Datenbanken,
Maildaten, Backups oder Docker-Volumes.

Alle Variablen kommen aus der kanonischen TriForce-Konfiguration
`/etc/triforce/triforce.env`. Die Datei wird als Docker-Compose-Env-Datei gelesen
und niemals als Shellscript `source`d.

Profiles: redis, wordpress, flarum, searxng, n8n, repository, mailserver.

## MCP und Berechtigungen

Das MCP-Tool `docker_stack` steuert nur diesen festen Blueprint und akzeptiert
keine freie Compose-Datei oder Shell-Kommandos. Das Debian-Paket gewaehrt dem
Dienstbenutzer absichtlich keine Docker-Socket-Rechte. Docker-Zugriff ist eine
separate Betreiberentscheidung, da Mitgliedschaft in der Docker-Gruppe
praktisch Root-Rechte am Host ermoeglicht. `validate` kann ohne laufende
Container verwendet werden; operative Aktionen benoetigen Docker-Berechtigung.
