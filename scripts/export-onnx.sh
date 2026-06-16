#!/usr/bin/env bash
# Export YOLO11 weights to ONNX for argus-pi. Run this on a DEV MACHINE that has
# Ultralytics installed (e.g. the original argus .venv) — NOT on the Pi. The
# resulting yolo11n.onnx is what ships to the Pi; the Pi never needs torch.
#
# Usage:
#   scripts/export-onnx.sh [MODEL] [IMGSZ]
# Defaults: MODEL=yolo11n.pt  IMGSZ=320
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"        # argus-pi/
MODEL="${1:-yolo11n.pt}"
IMGSZ="${2:-320}"

# Prefer the original argus project's venv (it has ultralytics); fall back to the
# python on PATH.
PY="$HERE/../argus/.venv/bin/python"
[ -x "$PY" ] || PY="python3"
echo "Using interpreter: $PY"

# The .pt weights live in the original argus project root.
WEIGHTS="$HERE/../argus/$MODEL"
[ -f "$WEIGHTS" ] || WEIGHTS="$MODEL"   # else let ultralytics resolve/download it

"$PY" - "$WEIGHTS" "$IMGSZ" "$HERE" <<'PY'
import sys, shutil
from pathlib import Path
from ultralytics import YOLO

weights, imgsz, dest_dir = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
m = YOLO(weights)
out = m.export(format="onnx", imgsz=imgsz, opset=12, simplify=True, dynamic=False)
out = Path(out)
target = dest_dir / out.name
if out.resolve() != target.resolve():
    shutil.copy2(out, target)
print(f"\nExported -> {target}  ({target.stat().st_size/1e6:.1f} MB, imgsz={imgsz})")
PY

echo "Done. Commit/rsync $HERE/$(basename "${MODEL%.pt}").onnx to the Pi."
