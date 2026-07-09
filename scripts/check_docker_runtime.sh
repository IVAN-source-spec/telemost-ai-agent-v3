#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "--- host pulse sources ---"
pactl list short sources | grep -E 'virtual_sink.monitor|pulse' || true

echo "--- docker compose status ---"
docker compose ps

echo "--- bot api ---"
curl -fsS http://127.0.0.1:8000/ && echo
curl -fsS http://127.0.0.1:8000/api/v1/node/status && echo

echo "--- container pulse ---"
docker exec telemost-bot pactl info | sed -n '1,10p'
docker exec telemost-bot pactl list short sources | grep -E 'virtual_sink.monitor|pulse' || true

echo "--- recent bot logs ---"
docker logs --tail=80 telemost-bot
