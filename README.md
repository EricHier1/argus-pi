# argus-pi

A Raspberry Pi build of **Argus** — a local computer-vision system that watches a
camera feed, identifies objects in real time with **YOLO11**, catalogs every
detection in a database with a timestamp, and lets you **search** past detections,
**browse** snapshots, and set **alerts**. Everything runs on the Pi — no cloud, no
accounts.

The difference from upstream Argus: detection runs on **ONNX Runtime** (no
PyTorch/Ultralytics, no GPU), so it fits a **Raspberry Pi 3B+** (4× Cortex-A53,
~1 GB RAM). Named after Argus Panoptes, the all-seeing giant of Greek myth.

## Hardware target
- Raspberry Pi 3B+ (quad Cortex-A53 @ 1.4 GHz, ~1 GB RAM, no usable GPU), 64-bit
  Debian/Raspberry Pi OS, Python 3.13.
- A camera: a USB webcam (`/dev/video0`) or an RTSP/ONVIF IP camera.
- Expect roughly a few inferences/sec for yolo11n @ 320 px on the CPU — plenty for
  surveillance, not real-time 25 fps.

## What it does
- **Live detection** — reads a USB webcam or an RTSP/IP camera and draws labeled
  boxes (color-coded by confidence) on a live feed.
- **Object tracking** — a lightweight IoU tracker assigns a stable ID per object,
  so a thing is cataloged once per appearance instead of every frame.
- **Catalog** — every detection stored (type, confidence, timestamp, track id,
  source, snapshot) in a local SQLite database.
- **Search** — find past detections by object type and time range.
- **Activity timeline** — a 24-hour chart of detection volume on the dashboard.
- **Gallery** — browse snapshots; arrow-key through them full-size, download, pin,
  or delete. Pinned snapshots survive cleanup.
- **Alerts** — rules like "person, confidence ≥ 0.6, between 22:00 and 06:00";
  matches are logged and shown on the dashboard.
- **On/off + clean shutdown** — turn detection off (the feed keeps streaming) or
  stop the server from the menu.
- **Storage cleanup** — auto-delete data older than N days and/or cap snapshot
  count (pinned items always kept).
- **Camera management** — add/remove cameras from the menu or by pasting a stream
  URL, without restarting.

## How it differs from upstream Argus
- `app/detector.py` — rewritten on **ONNX Runtime** (no torch/ultralytics). Same
  `Detector` interface, so the rest of the app is unchanged.
- `app/tracker.py` — a lightweight **IoU tracker** in place of ByteTrack.
- `app/config.py` / `app/capture.py` — ONNX defaults (`yolo11n.onnx`, cpu,
  `IMGSZ=320`) and USB-camera resolution/MJPG capping.
- **Single camera** recommended — multiple full detectors won't fit in ~1 GB RAM.
- **Video only** — none of upstream's positioner/PTZ/thermal *control* drivers.
- Ships as a **systemd service** and is accessed over **Tailscale** (below).

## Setup

### 1. Export the model (on a Mac/PC that has Ultralytics)
```bash
scripts/export-onnx.sh            # yolo11n.pt -> yolo11n.onnx (imgsz 320)
```

### 2. Copy the project to the Pi
Replace `<pi-user>`/`<pi-host>` with your Pi's login and address.
```bash
rsync -az --exclude .venv --exclude data ./ <pi-user>@<pi-host>:~/argus-pi/
```

### 3. Bootstrap + run (on the Pi)
```bash
cd ~/argus-pi
bash scripts/setup-pi.sh          # apt opencv/numpy, venv, pip onnxruntime/fastapi/uvicorn
bash scripts/install-service.sh   # renders + installs the systemd unit for the current user
```
Open `https://<pi-host>:8000` (accept the self-signed cert). On an untrusted /
client-isolating network (e.g. corporate guest wifi), use Tailscale instead — see
below.

## Remote access (Tailscale)
A guest/untrusted wifi causes two problems: it may **isolate clients** (so the
Pi's LAN IP becomes unreachable from your laptop), and argus has **no auth**, so
binding to the LAN exposes the dashboard to everyone on that network. Tailscale
fixes both — a private overlay that reaches the Pi from anywhere and lets argus
bind to the tailnet only.

**1. Install + bring up Tailscale on the Pi** (one time):
```bash
bash scripts/enable-tailscale.sh   # installs tailscale, runs `tailscale up`, prints the tailnet IP
```
`tailscaled` auto-starts on boot.

**2. Serve it on the tailnet with a trusted cert (recommended).** Requires
*HTTPS Certificates* + *MagicDNS* enabled in the Tailscale admin console. Have
argus listen on localhost only (`.env`):
```
ARGUS_BIND=local
ARGUS_HTTPS=0
```
then on the Pi:
```bash
sudo systemctl restart argus-pi
sudo tailscale serve --bg 8000      # HTTPS :443 -> http://127.0.0.1:8000
```
Tailscale proxies port 443 to argus with a real Let's Encrypt cert. Open
**`https://<pi-name>.<tailnet>.ts.net/`** from any tailnet device — **no port, no
warning, works in iOS Safari**, on wifi or cellular. argus is exposed only via the
serve proxy (not on the LAN or the tailnet IP directly), and `tailscale serve`
persists across reboots. To undo: `sudo tailscale serve --https=443 off`.

**Simpler alternative (self-signed).** Skip serve; set `ARGUS_HOST=<tailnet-ip>`
(from `tailscale ip -4`) and `ARGUS_HTTPS=1` to bind argus to the tailnet IP on
`:8000`. Reach it at `https://<pi-name>:8000` — works, but the self-signed cert
warns and **iOS Safari may refuse it**. (The systemd unit waits for Tailscale at
boot via `After/Wants=tailscaled.service` + an `ExecStartPre` IP check, so this
binds reliably after a reboot.)

## Operating
- Logs: `journalctl -u argus-pi -f`
- Restart after editing `.env`: `sudo systemctl restart argus-pi`
- Config lives in the gitignored `.env` (full reference in `app/config.py`).

## Configuration
All settings are `ARGUS_`-prefixed environment variables, read from `.env`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `ARGUS_SOURCES` | `0` | Comma-separated camera indexes and/or `rtsp://` URLs |
| `ARGUS_MODEL` | `yolo11n.onnx` | Exported ONNX model in the project root |
| `ARGUS_IMGSZ` | `320` | Inference input size (must match the export) |
| `ARGUS_CONFIDENCE` | `0.45` | Min confidence to record a detection |
| `ARGUS_DETECT_INTERVAL` | `1.0` | Seconds between inferences (raise to cut CPU) |
| `ARGUS_TRACK` | `1` | IoU tracker on (log once per appearance) |
| `ARGUS_CAPTURE_WIDTH/HEIGHT` | `0` | Cap a USB camera's resolution + request MJPG |
| `ARGUS_HOST` | `` | Bind address — set to the tailnet IP for tailnet-only |
| `ARGUS_PORT` / `ARGUS_HTTPS` | `8000` / `1` | Web UI port / self-signed TLS |

RTSP cameras (percent-encode `!`→`%21` in passwords):
```
ARGUS_SOURCES=rtsp://USER:%21yourpass@CAMERA_IP:554/onvif/profile1/media.smp
OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp|stimeout;5000000
```
Data lives in `data/` (SQLite DB + JPEG snapshots), gitignored.

## What it can detect
The default model recognizes the **80 COCO classes** — person, bicycle, car,
bus, truck, cat, dog, backpack, bottle, cell phone, etc. To detect something
outside that list you need different/custom weights; re-export them to ONNX with
`scripts/export-onnx.sh`. Note: on a thermal feed, an RGB-trained model detects
poorly — the feed still displays fine.

## Tuning performance
- `ARGUS_DETECT_INTERVAL` — biggest lever; 1.0 s keeps the Pi 3B+ sustainable.
- `ARGUS_IMGSZ` — 320 is fast; 416/640 are more accurate but much slower
  (re-export the ONNX to change).
- `ARGUS_ORT_THREADS` (default 4) — lower to 2 if running two cameras.
- The Pi 3B+ soft-throttles its clock above ~60 °C by design; a heatsink/fan
  keeps it at full speed for 24/7 use.

**Future speed upgrade — NCNN:** Tencent's ARM-optimized engine is typically
~1.5–2× ONNX Runtime on the Pi's CPU; export to NCNN and add a branch in
`detector.py`. ONNX Runtime was chosen first for its simpler aarch64 wheels.

## How it works
```
camera ─► capture.py ─► detector.py (ONNX) ─► db.py (SQLite)
              │              │ tracker.py        ▲
              │              └► alerts.py ───────┘
              └─────────────────────► server.py ─► web dashboard
                          maintenance.py (cleanup)
```
- `run.py` — entry point; launches the FastAPI/uvicorn server.
- `app/server.py` — REST API: stream, status, search, gallery, rules, settings,
  camera management, activity, shutdown, snapshots.
- `app/capture.py` — per-camera thread: grab frames, detect/track, log, save
  snapshots, evaluate alerts, publish the latest JPEG.
- `app/detector.py` — ONNX Runtime inference (letterbox, NMS, un-letterbox).
- `app/tracker.py` — lightweight IoU tracker.
- `app/db.py` — SQLite + search/gallery/activity/cleanup queries.
- `app/alerts.py`, `app/maintenance.py`, `app/devices.py`, `app/onvif.py` — alert
  rules, storage cleanup, camera enumeration, ONVIF discovery.
- `web/` — the dashboard (plain HTML/CSS/JS, no build step).

## Security & sharing
- The **source is safe to publish** — no secrets in code, SQL is parameterized,
  snapshot paths are traversal-checked, and `.gitignore` keeps `data/` (DB +
  snapshot images, which may show people) and `.env` (camera passwords) out of git.
- **No authentication.** Anyone who can reach the port can view feeds, browse/
  delete snapshots, and change settings — so bind it to the **tailnet only**
  (`ARGUS_HOST=<tailnet-ip>`) and never expose it on untrusted/guest wifi or the
  internet.

---
Based on Argus (local CV surveillance), adapted to run on the Raspberry Pi.
