#!/usr/bin/env bash
set -e
cd ~/triforce

echo "🔄 Restarting all Docker stacks..."

for stack in wordpress flarum searxng mailserver repository; do
    echo "📦 $stack..."
    cd docker/$stack
    docker compose down
    docker compose --env-file ../../.env up -d
    cd ../..
done

echo "✅ Fertig!"
docker ps --format "table {{.Names}}\t{{.Status}}"
