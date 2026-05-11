#!/usr/bin/env bash
set -euo pipefail

cd /mnt/data/recorder

docker compose pull
docker compose up -d --remove-orphans
docker image prune -f
