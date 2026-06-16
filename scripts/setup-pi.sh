#!/usr/bin/env bash
# One-time bootstrap for argus-pi ON THE RASPBERRY PI.
# Installs system OpenCV/NumPy via apt, creates a venv that can see them, and
# pip-installs the lightweight pure-Python deps (onnxruntime, fastapi, uvicorn).
#
# Run from the project root on the Pi:
#   cd ~/argus-pi && bash scripts/setup-pi.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

echo "==> Installing system packages (OpenCV, NumPy, venv, openssl)..."
sudo apt-get update
sudo apt-get install -y python3-opencv python3-numpy python3-venv openssl

echo "==> Creating venv (--system-site-packages so it can use apt's cv2/numpy)..."
python3 -m venv --system-site-packages .venv

echo "==> Installing Python deps into the venv..."
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements-pi.txt

echo "==> Verifying imports..."
./.venv/bin/python - <<'PY'
import cv2, numpy, onnxruntime, fastapi, uvicorn
print("cv2", cv2.__version__)
print("numpy", numpy.__version__)
print("onnxruntime", onnxruntime.__version__, onnxruntime.get_available_providers())
print("fastapi", fastapi.__version__)
print("OK: all imports succeeded")
PY

if [ ! -f "$HERE/yolo11n.onnx" ]; then
  echo "WARNING: yolo11n.onnx not found in $HERE."
  echo "  Export it on your Mac (scripts/export-onnx.sh) and rsync it here."
fi

echo "==> Done. Start manually with ./run.sh, or install the boot service:"
echo "    bash scripts/install-service.sh"
