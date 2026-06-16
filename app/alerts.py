"""Evaluate detections against active alert rules and record alerts.

A rule matches when: label matches, confidence >= min_conf, and the current
hour falls inside the rule's time window (windows may wrap past midnight, e.g.
22 -> 6 means 10pm to 6am). Each rule has a cooldown so it won't spam."""
import time
import threading

from . import config, db

_last_fired = {}        # (rule_id, source) -> last fire timestamp
_lock = threading.Lock()


def _in_window(hour, start_hour, end_hour):
    if start_hour is None or end_hour is None:
        return True
    if start_hour == end_hour:
        return True
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    # wraps midnight
    return hour >= start_hour or hour < end_hour


def evaluate(detections, save_snapshot, now=None, source=None):
    """Evaluate detections against active rules.

    detections: list of dicts (label, confidence, ...).
    save_snapshot: zero-arg callable returning a snapshot filename; only invoked
                   when a rule actually fires (so we don't write images needlessly).
    source: which camera these detections came from (for per-camera cooldown).
    Returns the list of fired alerts."""
    now = now or time.time()
    hour = time.localtime(now).tm_hour
    rules = [r for r in db.list_rules() if r["active"]]
    if not rules:
        return []

    fired = []
    for rule in rules:
        # best matching detection for this rule, if any
        candidates = [
            d for d in detections
            if d["label"] == rule["label"] and d["confidence"] >= rule["min_conf"]
        ]
        if not candidates:
            continue
        if not _in_window(hour, rule["start_hour"], rule["end_hour"]):
            continue

        key = (rule["id"], source)
        with _lock:
            if now - _last_fired.get(key, 0) < config.ALERT_COOLDOWN:
                continue
            _last_fired[key] = now

        best = max(candidates, key=lambda d: d["confidence"])
        snapshot = save_snapshot()
        src_tag = f" on {source}" if source is not None else ""
        msg = f"{rule['label']} detected{src_tag} (conf {best['confidence']:.2f})"
        db.insert_alert(rule["id"], rule["label"], best["confidence"], msg, snapshot)
        fired.append({"rule_id": rule["id"], "label": rule["label"],
                      "confidence": best["confidence"], "message": msg, "snapshot": snapshot})
    return fired
