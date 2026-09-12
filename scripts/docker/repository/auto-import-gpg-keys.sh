#!/usr/bin/env bash
set -euo pipefail

# AI Auto-Key-Importer
# Für fehlende apt Keys (NO_PUBKEY)

MISSING=$(apt-get update 2>&1 | grep 'NO_PUBKEY' | awk '{print $NF}' | sort -u)

if [ -z "$MISSING" ]; then
  echo -e "\e[1;32m✅ Keine fehlenden GPG-Keys gefunden.\e[0m"
  exit 0
fi

echo -e "\e[1;33m⚠️  Fehle GPG-Keys gefunden:\e[0m"
echo "$MISSING"

for key in $MISSING; do
  echo -e "\e[1;34m→ Importiere Key: $key\e[0m"
  gpg --keyserver keyserver.ubuntu.com --recv-keys "$key"
  gpg --export "$key" | sudo tee "/usr/share/keyrings/$key.gpg" > /dev/null
done

echo -e "\e[1;32m✅ Alle fehlenden Keys importiert.\e[0m"


