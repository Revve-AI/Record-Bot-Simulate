#!/usr/bin/env bash
# Per-deploy script. Invoked from the workflow via `sudo bash deploy.sh`
# with RELEASE_SHA in the environment, after the release tarball has been
# extracted into /mnt/data/recorder/releases/$RELEASE_SHA/.

set -euxo pipefail

RELEASE_SHA="${RELEASE_SHA:?RELEASE_SHA must be set}"
APP_USER=recorder
ROOT=/mnt/data/recorder
RELEASE_DIR="$ROOT/releases/$RELEASE_SHA"
VENV="$ROOT/.venv"
PY=python3.11

[ -d "$RELEASE_DIR" ] || { echo "release dir missing: $RELEASE_DIR" >&2; exit 1; }

chown -R "$APP_USER:$APP_USER" "$RELEASE_DIR"

if [ ! -x "$VENV/bin/python" ]; then
    sudo -u "$APP_USER" "$PY" -m venv "$VENV"
fi

sudo -u "$APP_USER" "$VENV/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$VENV/bin/pip" install -r "$RELEASE_DIR/requirements.txt"

sudo -u "$APP_USER" ln -sfn "$RELEASE_DIR" "$ROOT/current.new"
sudo -u "$APP_USER" mv -Tf "$ROOT/current.new" "$ROOT/current"

systemctl restart record-bot-simulate.service

# Keep the 3 most recent releases, prune the rest.
ls -1dt "$ROOT"/releases/*/ 2>/dev/null | tail -n +4 | xargs -r rm -rf
