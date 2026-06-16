"""Periodic storage cleanup. Reads the `retention_days` setting and prunes old
data on an interval (and once at startup). Runs in its own daemon thread."""
import threading
import time

from . import db

CHECK_INTERVAL = 1800  # seconds between cleanup passes (30 min)

_stop = threading.Event()
_thread = None
last_result = {"ran": False}


def run_once():
    """Run a single cleanup pass using the current retention settings
    (age in days and a max-snapshot count cap)."""
    global last_result
    days = int(float(db.get_setting("retention_days", "0")))
    max_snaps = int(float(db.get_setting("max_snapshots", "0")))
    result = db.cleanup(days)
    if result.get("skipped"):
        result = {"detections_deleted": 0, "alerts_deleted": 0, "files_deleted": 0}
    result["count_pruned"] = db.cleanup_by_count(max_snaps)
    result["ran_at"] = time.time()
    result["retention_days"] = days
    result["max_snapshots"] = max_snaps
    last_result = result
    return result


def _loop():
    while not _stop.is_set():
        try:
            run_once()
        except Exception as e:
            last_result["error"] = str(e)
        _stop.wait(CHECK_INTERVAL)


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()


def stop():
    _stop.set()
