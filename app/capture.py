"""Background capture + detection worker (one per camera).

Each worker runs in its own thread: grabs frames, throttles YOLO inference,
tracks objects, logs detections (once per appearance when tracking is on),
saves annotated snapshots, evaluates alert rules, and keeps the latest annotated
frame (JPEG) for the web stream."""
import os
import threading
import time

# Make OpenCV's FFmpeg backend use TCP for RTSP (more reliable than the UDP
# default) with a connect/read timeout, so IP cameras don't hang or stutter.
# Harmless for local/USB cameras (the options are ignored for non-RTSP sources).
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS",
                      "rtsp_transport;tcp|stimeout;5000000")
# Quiet the FFmpeg h264 decoder spam ("error while decoding MB ...") that floods
# the log on lossy RTSP streams — those are recoverable packet-loss artifacts.
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "8")   # AV_LOG_FATAL — hides decode errors

import cv2

from . import alerts, config, db
from .detector import Detector

# Box colors by confidence (BGR), from the Argus palette.
_COL_HIGH = (78, 187, 1)     # green  #01BB4E   (>= 0.75)
_COL_MED = (50, 223, 242)    # yellow #F2DF32   (>= 0.50)
_COL_LOW = (96, 102, 248)    # red    #F86660   (< 0.50)


def _conf_color(c):
    if c >= 0.75:
        return _COL_HIGH
    if c >= 0.50:
        return _COL_MED
    return _COL_LOW


# Global toggle for drawing boxes on the live feed (snapshots keep them regardless).
_show_boxes = True


def set_show_boxes(on):
    global _show_boxes
    _show_boxes = bool(on)


def get_show_boxes():
    return _show_boxes


class CameraWorker:
    def __init__(self, source=None):
        self.detector = None
        self.cap = None
        self.source = str(source if source is not None else config.SOURCE)
        self._thread = None
        self._stop = threading.Event()
        self._frame_lock = threading.Lock()
        self._latest_jpeg = None
        self._seen = {}            # track_id -> last-seen ts (for log dedup)
        self.status = {
            "running": False,
            "paused": False,
            "source": self.source,
            "model": config.MODEL,
            "device": config.DEVICE,
            "tracking": config.TRACK,
            "error": None,
            "fps": 0.0,
            "last_detection_count": 0,
        }

    def _source_value(self):
        """The current source as an int (camera index) or str (URL)."""
        s = str(self.source)
        return int(s) if s.isdigit() else s

    # --- lifecycle ---------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def pause(self):
        """Turn detection OFF: the live feed keeps streaming, but YOLO inference,
        snapshots, detection logging, and alerts stop (that's where the CPU goes)."""
        self.status["paused"] = True
        self.status["last_detection_count"] = 0
        if not (self._thread and self._thread.is_alive()):
            self.start()   # keep the feed alive even if it wasn't running

    def resume(self):
        """Turn detection back ON."""
        self.status["paused"] = False
        if not (self._thread and self._thread.is_alive()):
            self.start()

    def set_source(self, source):
        """Switch this worker's video input at runtime: stop, swap, restart."""
        self.stop()
        self.source = str(source)
        self.status["source"] = self.source
        self.status["error"] = None
        self.status["fps"] = 0.0
        self._seen.clear()
        with self._frame_lock:
            self._latest_jpeg = None
        self.start()
        return self.source

    # --- main loop ---------------------------------------------------------
    def _run(self):
        src = self._source_value()
        self.cap = cv2.VideoCapture(src)
        if not self.cap.isOpened():
            self.status["error"] = (
                f"could not open camera source {src!r}. "
                "Check the camera is connected and not in use by another app, "
                "and that the app has camera permission in System Settings > Privacy."
            )
            return

        # On the Pi, cap a local USB camera's resolution and request MJPG (which
        # most USB webcams stream natively). This keeps encode/snapshot CPU+RAM
        # low. Skipped for URL/RTSP sources and when CAPTURE_WIDTH/HEIGHT are 0.
        if str(self.source).isdigit() and config.CAPTURE_WIDTH and config.CAPTURE_HEIGHT:
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAPTURE_WIDTH)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAPTURE_HEIGHT)

        # Load YOLO lazily and non-fatally: if it fails, the live feed still
        # streams (detection is just disabled).
        try:
            self.detector = Detector()
            self.status["device"] = self.detector.device
            self.status["tracking"] = self.detector.track
        except Exception as e:
            self.detector = None
            self.status["error"] = f"model load failed: {e}"

        self.status["running"] = True
        self.status["error"] = None
        last_detect = 0.0
        last_snapshot = 0.0
        last_fps_t = time.time()
        frames = 0
        last_detections = []   # most recent boxes, redrawn every frame to avoid flicker

        while not self._stop.is_set():
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.05)
                continue

            frames += 1
            now = time.time()

            if now - last_fps_t >= 1.0:
                self.status["fps"] = round(frames / (now - last_fps_t), 1)
                frames = 0
                last_fps_t = now

            # Detection OFF (paused) or no model: keep streaming raw frames, but
            # run no YOLO / snapshots / logging / alerts.
            if self.status["paused"] or self.detector is None:
                last_detections = []
                self._publish(frame)
                continue

            if now - last_detect >= config.DETECT_INTERVAL:
                last_detect = now
                detections = self.detector.detect(frame)
                last_detections = detections
                self.status["last_detection_count"] = len(detections)

                to_log = self._select_to_log(detections, now)

                if detections:
                    # Snapshots are saved RAW (no boxes); the box geometry is stored
                    # alongside so the gallery can overlay + toggle boxes in the browser.
                    snapshot_name = None
                    if to_log and now - last_snapshot >= config.SNAPSHOT_INTERVAL:
                        last_snapshot = now
                        snapshot_name = self._save_snapshot(frame, now, detections)

                    if to_log:
                        rows = [{
                            "ts": now, "source": self.source,
                            "label": d["label"], "confidence": d["confidence"],
                            "track_id": d.get("track_id"), "event_id": d.get("event_id"),
                            "x1": d["x1"], "y1": d["y1"], "x2": d["x2"], "y2": d["y2"],
                            "snapshot": snapshot_name,
                        } for d in to_log]
                        db.insert_detections(rows)

                    # Alerts evaluate on ALL current detections (cooldown stops spam);
                    # the snapshot is saved lazily only if a rule actually fires.
                    cache = {"name": snapshot_name}

                    def save_alert_snapshot():
                        if cache["name"] is None:
                            cache["name"] = self._save_snapshot(frame, now, detections)
                        return cache["name"]

                    alerts.evaluate(detections, save_alert_snapshot, now=now, source=self.source)

            # Boxes on the live feed are toggleable; snapshots above always keep them.
            display = self._draw(frame, last_detections) if _show_boxes else frame
            self._publish(display)

        self.status["running"] = False
        if self.cap:
            self.cap.release()

    def _cadence_for(self, age):
        """Snapshot interval (seconds) based on how long an object has been in
        frame, or None to stop logging it."""
        if age < config.DWELL_TIER_1:
            return config.PERSIST_INTERVAL    # every 2s for the first minute
        if age < config.DWELL_TIER_2:
            return 60.0                       # 1/min until 10 min
        if age < config.DWELL_TIER_3:
            return 3600.0                     # 1/hour until 1 hour
        return None                           # after 1 hour: ignore until it leaves

    def _select_to_log(self, detections, now):
        """With tracking on, log each object on an adaptive cadence that slows the
        longer it lingers (see _cadence_for). Each object appearance gets a stable
        event_id so all its snapshots can be grouped later. Without tracking, log
        all detections (throttled by detection cycle)."""
        if not config.TRACK:
            return [dict(d, event_id=None) for d in detections]
        to_log = []
        for d in detections:
            tid = d.get("track_id")
            if tid is None:
                continue   # tracker hasn't locked on yet; it'll log once it assigns an id
            st = self._seen.get(tid)
            if st is None:   # new appearance -> new event group
                st = {"t0": now, "last": 0.0, "seen": now,
                      "event": f"{self.source}-{tid}-{int(now * 1000)}"}
                self._seen[tid] = st
            st["seen"] = now
            cadence = self._cadence_for(now - st["t0"])
            if cadence is not None and now - st["last"] >= cadence:
                st["last"] = now
                to_log.append(dict(d, event_id=st["event"]))
        # forget tracks that have left the scene (so a re-entry starts fresh)
        gone = [t for t, s in self._seen.items() if now - s["seen"] > config.LEAVE_GAP]
        for t in gone:
            self._seen.pop(t, None)
        return to_log

    # --- helpers -----------------------------------------------------------
    def _draw(self, frame, detections):
        """Draw bounding boxes + labels (color-coded by confidence)."""
        if not detections:
            return frame
        img = frame.copy()
        for d in detections:
            x1, y1, x2, y2 = int(d["x1"]), int(d["y1"]), int(d["x2"]), int(d["y2"])
            color = _conf_color(d["confidence"])
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
            label = f'{d["label"]} {d["confidence"] * 100:.0f}%'
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(img, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
            cv2.putText(img, label, (x1 + 2, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (20, 20, 20), 1, cv2.LINE_AA)
        return img

    def _save_snapshot(self, frame, ts, detections):
        """Save the RAW frame and persist its box geometry separately so the
        gallery can overlay (and toggle) boxes in the browser."""
        name = f"{int(ts * 1000)}.jpg"
        cv2.imwrite(str(config.SNAPSHOT_DIR / name), frame)
        db.save_snapshot_boxes(name, [{
            "label": d["label"], "conf": round(d["confidence"], 3),
            "x1": round(d["x1"]), "y1": round(d["y1"]),
            "x2": round(d["x2"]), "y2": round(d["y2"]),
        } for d in detections])
        return name

    def _publish(self, frame):
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            with self._frame_lock:
                self._latest_jpeg = buf.tobytes()

    def get_jpeg(self):
        with self._frame_lock:
            return self._latest_jpeg

    def mjpeg_frames(self):
        """Generator yielding multipart MJPEG chunks for the live stream."""
        boundary = b"--frame"
        while True:
            jpeg = self.get_jpeg()
            if jpeg is not None:
                yield (boundary + b"\r\nContent-Type: image/jpeg\r\n"
                       + f"Content-Length: {len(jpeg)}\r\n\r\n".encode()
                       + jpeg + b"\r\n")
            time.sleep(0.04)  # ~25 fps cap on the stream
