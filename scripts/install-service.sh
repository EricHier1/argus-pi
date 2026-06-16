#!/usr/bin/env bash
# Render deploy/argus-pi.service for the current user + directory and install it.
# Run from the project root on the Pi:  bash scripts/install-service.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(whoami)"
UNIT=/etc/systemd/system/argus-pi.service

echo "==> Installing argus-pi.service for user=$USER_NAME dir=$HERE"
sed -e "s#__USER__#${USER_NAME}#g" -e "s#__WORKDIR__#${HERE}#g" \
    "$HERE/deploy/argus-pi.service" | sudo tee "$UNIT" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now argus-pi
echo "==> Done. Status:"
systemctl --no-pager --lines=0 status argus-pi || true
