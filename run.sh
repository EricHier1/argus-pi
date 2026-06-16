#!/usr/bin/env bash
# Start argus-pi manually (the systemd service runs the same thing). Config is
# read from .env by app/config.py at startup.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"
exec ./.venv/bin/python run.py
