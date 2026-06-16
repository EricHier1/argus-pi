"""Central configuration. Override any value with an environment variable
(prefix ARGUS_), e.g. ARGUS_SOURCE=1 to use a second camera. A gitignored `.env`
file in the project root is loaded at startup — edit it to change cameras/IPs."""
import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
DB_PATH = DATA_DIR / "argus.db"
WEB_DIR = BASE_DIR / "web"


def _load_dotenv():
    """Load KEY=VALUE lines from .env into the environment (real env vars win)."""
    f = BASE_DIR / ".env"
    if not f.exists():
        return
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        # Allow an inline comment ("KEY=value   # note"): a '#' that follows
        # whitespace starts the comment. A '#' with no leading space is kept,
        # so it stays safe inside a value such as a URL or password.
        val = re.split(r"\s#", val, maxsplit=1)[0].strip()
        os.environ.setdefault(key.strip(), val)


_load_dotenv()


def _env(name: str, default):
    # ARGUS_ is the current prefix; CVISION_ kept as a fallback for older configs.
    return os.environ.get(f"ARGUS_{name}", os.environ.get(f"CVISION_{name}", default))


# --- Camera source ---------------------------------------------------------
# An integer (0 = default built-in/USB cam, 1 = next camera, ...) OR a URL
# string for an IP/phone camera (e.g. "http://192.168.1.50:4747/video").
SOURCE = _env("SOURCE", "0")

# --- Detection -------------------------------------------------------------
# argus-pi runs inference via ONNX Runtime (no torch), so MODEL is an exported
# .onnx file, not a .pt. Export it on a dev machine with scripts/export-onnx.sh.
MODEL = _env("MODEL", "yolo11n.onnx")        # ONNX model in the project root
CONFIDENCE = float(_env("CONFIDENCE", "0.45"))  # minimum confidence to record a detection
# Square inference input the model was exported at (read from the model if it
# reports a fixed size; this is the fallback). 320 is a good speed/accuracy
# trade on the Pi 3B+; 416/640 are more accurate but markedly slower.
IMGSZ = int(_env("IMGSZ", "320"))
# IoU threshold for non-max suppression of overlapping detections.
IOU_THRES = float(_env("IOU_THRES", "0.45"))
# ONNX Runtime intra-op threads. The Pi 3B+ has 4 cores; using all of them gives
# the fastest single-frame inference.
ORT_THREADS = int(_env("ORT_THREADS", "4"))
# Seconds between detections. 0 = detect every frame, which keeps the drawn boxes
# perfectly aligned with the live image (raise it to trade alignment for less load).
DETECT_INTERVAL = float(_env("DETECT_INTERVAL", "0.0"))
SNAPSHOT_INTERVAL = float(_env("SNAPSHOT_INTERVAL", "2.0"))  # min seconds between saved frame snapshots
# Adaptive snapshot cadence for an object that lingers in frame:
#   first PERSIST_INTERVAL seconds apart, then slower the longer it stays.
PERSIST_INTERVAL = float(_env("PERSIST_INTERVAL", "2.0"))   # 0–1 min: every 2s
DWELL_TIER_1 = float(_env("DWELL_TIER_1", "60"))            # after 1 min -> 1/min
DWELL_TIER_2 = float(_env("DWELL_TIER_2", "600"))           # after 10 min -> 1/hour
DWELL_TIER_3 = float(_env("DWELL_TIER_3", "3600"))          # after 1 hour -> stop logging
# If a track isn't seen for this long it's considered to have left; re-entry
# restarts its cadence from the top.
LEAVE_GAP = float(_env("LEAVE_GAP", "60"))

# Inference device. The Pi 3B+ has no usable GPU, so this is always cpu; the
# value is kept only so status/UI fields stay populated.
DEVICE = _env("DEVICE", "cpu")
# Object tracking: assigns a stable ID per object so it's logged once per appearance
# (not every frame). On the Pi this is a lightweight IoU tracker (app/tracker.py),
# not ByteTrack. Set ARGUS_TRACK=0 to disable.
TRACK = str(_env("TRACK", "1")).lower() not in ("0", "false", "no")
# IoU tracker tuning: TRACK_IOU is the min overlap to consider two frames' boxes
# the same object; TRACK_MAX_AGE is how many detection cycles a track survives
# unseen before its id is recycled.
TRACK_IOU = float(_env("TRACK_IOU", "0.3"))
TRACK_MAX_AGE = int(_env("TRACK_MAX_AGE", "30"))

# --- Cameras ---------------------------------------------------------------
# One or more sources, comma-separated. Each is a camera index (0,1,…) or a
# stream URL. Defaults to the single SOURCE above.
SOURCES = [s.strip() for s in str(_env("SOURCES", SOURCE)).split(",") if s.strip()]

# Cap the capture resolution of *local* USB cameras (camera-index sources). 0
# leaves the camera at its default. On the Pi this keeps JPEG-encode + snapshot
# cost down and requests MJPG, which most USB webcams stream natively over USB 2.
CAPTURE_WIDTH = int(_env("CAPTURE_WIDTH", "0"))
CAPTURE_HEIGHT = int(_env("CAPTURE_HEIGHT", "0"))

# --- Server ----------------------------------------------------------------
# How the server is exposed:
#   lan       - 0.0.0.0, reachable by anything on your wifi (self-signed cert) [home mode]
#   local     - 127.0.0.1 only, this computer (self-signed cert)
#   tailscale - bound to the Tailscale IP only (trusted cert, works over cell,
#               NOT exposed on wifi)
BIND = _env("BIND", "lan").lower()
HOST = _env("HOST", "")          # explicit override of the bind address (advanced)
PORT = int(_env("PORT", "8000"))
# Serve over HTTPS with a self-signed cert (browsers warn once). ARGUS_HTTPS=0 for http.
HTTPS = str(_env("HTTPS", "1")).lower() not in ("0", "false", "no")
# Access-log noise control for the endpoints the web UI polls constantly
# (/frame, /stream, /api/status). 0 = don't log them at all (default); 1 = log
# every one (original behavior); N = log 1 of every N. Other requests always log.
ACCESS_LOG_EVERY = int(_env("ACCESS_LOG_EVERY", "0"))

# --- Alerts ----------------------------------------------------------------
ALERT_COOLDOWN = float(_env("ALERT_COOLDOWN", "30"))  # seconds before the same rule can fire again


def source_value():
    """Return SOURCE as an int if it looks like a camera index, else the raw string (URL)."""
    s = str(SOURCE)
    return int(s) if s.isdigit() else s
