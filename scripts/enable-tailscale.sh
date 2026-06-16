#!/usr/bin/env bash
# Install + bring up Tailscale on the Pi, then print the tailnet IP to set as
# ARGUS_HOST in .env (binds argus to the tailnet only — off the LAN/guest wifi).
# Run on the Pi:  bash scripts/enable-tailscale.sh
set -euo pipefail

if ! command -v tailscale >/dev/null 2>&1; then
  echo "==> Installing Tailscale..."
  curl -fsSL https://tailscale.com/install.sh | sh
fi

echo "==> Bringing up Tailscale (sign in via the printed URL if prompted)..."
sudo tailscale up

IP="$(tailscale ip -4 | head -1)"
NAME="$(hostname)"
echo
echo "Tailscale is up. tailnet IP: ${IP}  (MagicDNS name: ${NAME})"
echo
echo "To serve argus ONLY on the tailnet (not the LAN/guest wifi), set in .env:"
echo "    ARGUS_HOST=${IP}"
echo "then:  sudo systemctl restart argus-pi"
echo "Access:  https://${NAME}:8000   or   https://${IP}:8000"
