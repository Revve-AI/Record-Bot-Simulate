#!/usr/bin/env bash
# Idempotent VM bootstrap for the direct-deploy pipeline.
# Invoked from the GitHub Actions workflow via `sudo bash -s` over IAP SSH.
# Safe to run on every deploy — all steps short-circuit when already applied.

set -euxo pipefail

APP_USER=recorder
APP_ROOT=/mnt/data/recorder
PY=python3.11

if ! command -v "$PY" >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        software-properties-common
    add-apt-repository -y ppa:deadsnakes/ppa
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
fi

DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    "$PY" "${PY}-venv" ffmpeg libsndfile1 curl ca-certificates

id -u "$APP_USER" >/dev/null 2>&1 || useradd -r -m -d "/home/$APP_USER" -s /bin/bash "$APP_USER"

install -d -o "$APP_USER" -g "$APP_USER" \
    "$APP_ROOT" \
    "$APP_ROOT/releases" \
    "$APP_ROOT/input" \
    "$APP_ROOT/output" \
    "$APP_ROOT/cache" \
    "$APP_ROOT/cache/torch" \
    "$APP_ROOT/cache/huggingface" \
    "$APP_ROOT/tmp" \
    "$APP_ROOT/tmp/gradio"

UNIT_PATH=/etc/systemd/system/record-bot-simulate.service
UNIT_NEW="${UNIT_PATH}.new"

cat >"$UNIT_NEW" <<'UNIT'
[Unit]
Description=Record Bot Simulate
After=network.target

[Service]
Type=simple
User=recorder
Group=recorder
WorkingDirectory=/mnt/data/recorder/current
Environment=PORT=7860
Environment=SHARE=0
Environment=GRADIO_TEMP_DIR=/mnt/data/recorder/tmp/gradio
Environment=TORCH_HOME=/mnt/data/recorder/cache/torch
Environment=HF_HOME=/mnt/data/recorder/cache/huggingface
ExecStart=/mnt/data/recorder/.venv/bin/python app.py
Restart=on-failure
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
UNIT

if ! cmp -s "$UNIT_NEW" "$UNIT_PATH" 2>/dev/null; then
    mv "$UNIT_NEW" "$UNIT_PATH"
    systemctl daemon-reload
else
    rm "$UNIT_NEW"
fi

systemctl enable record-bot-simulate.service
