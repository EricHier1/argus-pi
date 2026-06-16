"""SQLite storage layer. One connection per thread (sqlite3 default), guarded
by a module lock so the capture thread and the web server can share writes."""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager

from . import config

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS detections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,          -- unix epoch seconds
    source      TEXT    NOT NULL,
    label       TEXT    NOT NULL,
    confidence  REAL    NOT NULL,
    track_id    INTEGER,                    -- ByteTrack id, or NULL
    event_id    TEXT,                       -- stable id per object appearance (grouping)
    x1 REAL, y1 REAL, x2 REAL, y2 REAL,    -- bounding box
    snapshot    TEXT                        -- filename in data/snapshots, or NULL
);
CREATE INDEX IF NOT EXISTS idx_det_ts    ON detections(ts);
CREATE INDEX IF NOT EXISTS idx_det_label ON detections(label);

CREATE TABLE IF NOT EXISTS alert_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT    NOT NULL,          -- object type to watch, e.g. 'person'
    min_conf    REAL    NOT NULL DEFAULT 0.5,
    start_hour  INTEGER,                    -- 0-23, NULL = any time
    end_hour    INTEGER,                    -- 0-23, NULL = any time (window may wrap midnight)
    active      INTEGER NOT NULL DEFAULT 1,
    created     REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    rule_id     INTEGER,
    label       TEXT    NOT NULL,
    confidence  REAL,
    message     TEXT,
    snapshot    TEXT
);
CREATE INDEX IF NOT EXISTS idx_alert_ts ON alerts(ts);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- snapshots the user pinned to permanent storage (never removed by cleanup)
CREATE TABLE IF NOT EXISTS kept_snapshots (
    snapshot TEXT PRIMARY KEY,
    ts       REAL NOT NULL,
    note     TEXT
);

-- box geometry per snapshot (image is saved RAW; boxes are drawn as an overlay
-- in the browser so they can be toggled on/off).
CREATE TABLE IF NOT EXISTS snapshot_boxes (
    snapshot TEXT PRIMARY KEY,
    boxes    TEXT NOT NULL   -- JSON: [{label, conf, x1, y1, x2, y2}, ...]
);
"""


def init():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.executescript(SCHEMA)
        # migrate older DBs that predate newer columns
        cols = {r["name"] for r in c.execute("PRAGMA table_info(detections)")}
        if "track_id" not in cols:
            c.execute("ALTER TABLE detections ADD COLUMN track_id INTEGER")
        if "event_id" not in cols:
            c.execute("ALTER TABLE detections ADD COLUMN event_id TEXT")
        # index created after migration so the column is guaranteed to exist
        c.execute("CREATE INDEX IF NOT EXISTS idx_det_event ON detections(event_id)")


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with _lock:
            yield conn
            conn.commit()
    finally:
        conn.close()


# --- Detections ------------------------------------------------------------
def insert_detections(rows):
    """rows: list of dicts with ts, source, label, confidence, x1..y2, snapshot."""
    if not rows:
        return
    with connect() as c:
        c.executemany(
            """INSERT INTO detections (ts, source, label, confidence, track_id, event_id,
                                       x1, y1, x2, y2, snapshot)
               VALUES (:ts, :source, :label, :confidence, :track_id, :event_id,
                       :x1, :y1, :x2, :y2, :snapshot)""",
            rows,
        )


def search_detections(label=None, start=None, end=None, limit=200):
    q = "SELECT * FROM detections WHERE 1=1"
    params = {}
    if label:
        q += " AND label = :label"
        params["label"] = label
    if start is not None:
        q += " AND ts >= :start"
        params["start"] = start
    if end is not None:
        q += " AND ts <= :end"
        params["end"] = end
    q += " ORDER BY ts DESC LIMIT :limit"
    params["limit"] = limit
    with connect() as c:
        return [dict(r) for r in c.execute(q, params).fetchall()]


def gallery(label=None, limit=30, pinned=False, before=None):
    """Gallery grouped by object appearance, paginated by a `before` timestamp
    cursor (return groups whose newest pic is older than `before`). Each row is
    one object (an event_id, or a lone snapshot when untracked), with its newest
    snapshot as the tile, a count of pictures, and whether any are pinned.

    Returns dicts: {gkey, snapshot, ts, labels, n, source, kept}.
    SQLite's bare-column rule makes `snapshot`/`source` come from the MAX(ts) row,
    so the tile is the most recent picture of that object."""
    # MAX(ts) must be the ONLY min/max aggregate here so SQLite makes the bare
    # columns (snapshot, source) come from the latest row -> newest pic as tile.
    q = """SELECT COALESCE(event_id, snapshot) AS gkey,
                  MAX(ts) AS ts,
                  snapshot AS snapshot,
                  source AS source,
                  GROUP_CONCAT(DISTINCT label) AS labels,
                  COUNT(DISTINCT snapshot) AS n,
                  (SUM(CASE WHEN snapshot IN (SELECT snapshot FROM kept_snapshots)
                            THEN 1 ELSE 0 END) > 0) AS kept
           FROM detections
           WHERE snapshot IS NOT NULL"""
    params = {}
    if label:
        q += """ AND COALESCE(event_id, snapshot) IN (
                    SELECT COALESCE(event_id, snapshot) FROM detections
                    WHERE label = :label AND snapshot IS NOT NULL)"""
        params["label"] = label
    q += " GROUP BY gkey"
    having = []
    if pinned:
        having.append("kept = 1")
    if before is not None:
        having.append("MAX(ts) < :before")
        params["before"] = before
    if having:
        q += " HAVING " + " AND ".join(having)
    q += " ORDER BY ts DESC LIMIT :limit"
    params["limit"] = limit
    with connect() as c:
        return [dict(r) for r in c.execute(q, params).fetchall()]


def gallery_group(gkey, limit=500):
    """All snapshots belonging to one object group (event_id or lone snapshot),
    oldest first, for the expanded view."""
    q = """SELECT d.snapshot AS snapshot, MIN(d.ts) AS ts,
                  GROUP_CONCAT(DISTINCT d.label) AS labels,
                  MAX(CASE WHEN d.snapshot IN (SELECT snapshot FROM kept_snapshots)
                           THEN 1 ELSE 0 END) AS kept
           FROM detections d
           WHERE COALESCE(d.event_id, d.snapshot) = :gkey AND d.snapshot IS NOT NULL
           GROUP BY d.snapshot ORDER BY ts ASC LIMIT :limit"""
    with connect() as c:
        return [dict(r) for r in c.execute(q, {"gkey": gkey, "limit": limit}).fetchall()]


def label_summary():
    """Counts per label across all detections, for the dashboard filter + stats."""
    with connect() as c:
        rows = c.execute(
            "SELECT label, COUNT(*) n, MAX(ts) last_seen FROM detections GROUP BY label ORDER BY n DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def analytics():
    """Aggregate stats for the Analytics tab."""
    with connect() as c:
        det = c.execute("SELECT COUNT(*) n FROM detections").fetchone()["n"]
        alr = c.execute("SELECT COUNT(*) n FROM alerts").fetchone()["n"]
        snaps = c.execute(
            "SELECT COUNT(DISTINCT snapshot) n FROM detections WHERE snapshot IS NOT NULL"
        ).fetchone()["n"]
        types = c.execute("SELECT COUNT(DISTINCT label) n FROM detections").fetchone()["n"]
        by_label = [dict(r) for r in c.execute(
            "SELECT label, COUNT(*) n FROM detections GROUP BY label ORDER BY n DESC LIMIT 15")]
        by_camera = [dict(r) for r in c.execute(
            "SELECT source, COUNT(*) n FROM detections GROUP BY source ORDER BY n DESC")]
        hours = [0] * 24
        for r in c.execute(
            "SELECT CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) h, "
            "COUNT(*) n FROM detections GROUP BY h"):
            if r["h"] is not None:
                hours[r["h"]] = r["n"]
    return {
        "totals": {"detections": det, "alerts": alr, "snapshots": snaps, "types": types},
        "by_label": by_label, "by_camera": by_camera, "by_hour": hours,
    }


def activity(hours=24, buckets=24):
    """Detection counts bucketed over the last `hours`, for the timeline chart.
    Returns {start, width_s, counts:[...]} with zero-filled buckets."""
    now = time.time()
    start = now - hours * 3600
    width = (hours * 3600) / buckets
    counts = [0] * buckets
    with connect() as c:
        for r in c.execute("SELECT ts FROM detections WHERE ts >= ?", (start,)):
            i = int((r["ts"] - start) / width)
            if 0 <= i < buckets:
                counts[i] += 1
    return {"start": start, "width_s": width, "counts": counts}


# --- Alert rules -----------------------------------------------------------
def add_rule(label, min_conf=0.5, start_hour=None, end_hour=None):
    with connect() as c:
        cur = c.execute(
            "INSERT INTO alert_rules (label, min_conf, start_hour, end_hour, active, created) "
            "VALUES (?,?,?,?,1,?)",
            (label, min_conf, start_hour, end_hour, time.time()),
        )
        return cur.lastrowid


def list_rules():
    with connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM alert_rules ORDER BY created DESC").fetchall()]


def set_rule_active(rule_id, active):
    with connect() as c:
        c.execute("UPDATE alert_rules SET active=? WHERE id=?", (1 if active else 0, rule_id))


def delete_rule(rule_id):
    with connect() as c:
        c.execute("DELETE FROM alert_rules WHERE id=?", (rule_id,))


# --- Alerts ----------------------------------------------------------------
def insert_alert(rule_id, label, confidence, message, snapshot):
    with connect() as c:
        c.execute(
            "INSERT INTO alerts (ts, rule_id, label, confidence, message, snapshot) VALUES (?,?,?,?,?,?)",
            (time.time(), rule_id, label, confidence, message, snapshot),
        )


def count_alerts():
    with connect() as c:
        return c.execute("SELECT COUNT(*) AS n FROM alerts").fetchone()["n"]


def recent_alerts(limit=100, since=None, before=None):
    """Alerts newest-first. `since` (ts >) for polling new ones; `before` (ts <)
    is the pagination cursor for the Alerts tab."""
    q = "SELECT * FROM alerts WHERE 1=1"
    params = []
    if since is not None:
        q += " AND ts > ?"
        params.append(since)
    if before is not None:
        q += " AND ts < ?"
        params.append(before)
    q += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)
    with connect() as c:
        return [dict(r) for r in c.execute(q, params).fetchall()]


# --- Settings --------------------------------------------------------------
def get_setting(key, default=None):
    with connect() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with connect() as c:
        c.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


# --- Camera names ----------------------------------------------------------
def get_camera_names():
    """{source: custom name} dict."""
    raw = get_setting("camera_names", "{}")
    try:
        return json.loads(raw)
    except Exception:
        return {}


def set_camera_name(source, name):
    names = get_camera_names()
    source = str(source)
    name = (name or "").strip()
    if name:
        names[source] = name
    else:
        names.pop(source, None)
    set_setting("camera_names", json.dumps(names))


# --- Bulk delete -----------------------------------------------------------
def _prune_orphan_files(referenced):
    removed = 0
    if config.SNAPSHOT_DIR.exists():
        for f in config.SNAPSHOT_DIR.iterdir():
            if f.name not in referenced:
                try:
                    f.unlink(); removed += 1
                except OSError:
                    pass
    return removed


def delete_all_gallery():
    """Delete every detection + its snapshot (the whole gallery). Alert pictures
    that were shared with the gallery are unlinked; alert-only pics are kept."""
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM detections").fetchone()[0]
        gal = [r["snapshot"] for r in c.execute(
            "SELECT DISTINCT snapshot FROM detections WHERE snapshot IS NOT NULL")]
        c.execute("DELETE FROM detections")
        c.execute("DELETE FROM snapshot_boxes")
        c.execute("DELETE FROM kept_snapshots")
        for s in gal:
            c.execute("UPDATE alerts SET snapshot=NULL WHERE snapshot=?", (s,))
        ref = {r["snapshot"] for r in c.execute(
            "SELECT DISTINCT snapshot FROM alerts WHERE snapshot IS NOT NULL")}
    return {"detections_deleted": n, "files_deleted": _prune_orphan_files(ref)}


def delete_all_alerts():
    """Delete every alert. Snapshot files referenced only by alerts are removed;
    gallery pictures (referenced by detections) are kept."""
    with connect() as c:
        n = c.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        c.execute("DELETE FROM alerts")
        ref = {r["snapshot"] for r in c.execute(
            "SELECT DISTINCT snapshot FROM detections WHERE snapshot IS NOT NULL")}
        ref |= {r["snapshot"] for r in c.execute("SELECT snapshot FROM kept_snapshots")}
    return {"alerts_deleted": n, "files_deleted": _prune_orphan_files(ref)}


# --- Pinned (kept) snapshots ----------------------------------------------
def set_kept(snapshot, kept, note=None):
    with connect() as c:
        if kept:
            row = c.execute("SELECT ts FROM detections WHERE snapshot=? LIMIT 1", (snapshot,)).fetchone()
            ts = row["ts"] if row else 0.0
            c.execute(
                "INSERT OR REPLACE INTO kept_snapshots (snapshot, ts, note) VALUES (?,?,?)",
                (snapshot, ts, note),
            )
        else:
            c.execute("DELETE FROM kept_snapshots WHERE snapshot=?", (snapshot,))


def is_kept(snapshot):
    with connect() as c:
        return c.execute("SELECT 1 FROM kept_snapshots WHERE snapshot=?", (snapshot,)).fetchone() is not None


def list_kept():
    with connect() as c:
        return {r["snapshot"] for r in c.execute("SELECT snapshot FROM kept_snapshots").fetchall()}


def save_snapshot_boxes(snapshot, boxes):
    """Store the box geometry for a (raw) snapshot, for the gallery overlay."""
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO snapshot_boxes (snapshot, boxes) VALUES (?, ?)",
                  (snapshot, json.dumps(boxes)))


def get_snapshot_boxes(snapshot):
    with connect() as c:
        r = c.execute("SELECT boxes FROM snapshot_boxes WHERE snapshot=?", (snapshot,)).fetchone()
        return json.loads(r["boxes"]) if r else []


def delete_group(gkey):
    """Delete every snapshot belonging to one object group. Returns the count."""
    snaps = [r["snapshot"] for r in gallery_group(gkey)]
    for s in snaps:
        delete_snapshot(s)
    return len(snaps)


def delete_snapshot(snapshot):
    """Delete a snapshot everywhere: its detection rows, alert refs, pin, and file."""
    with connect() as c:
        c.execute("DELETE FROM detections WHERE snapshot=?", (snapshot,))
        c.execute("DELETE FROM kept_snapshots WHERE snapshot=?", (snapshot,))
        c.execute("DELETE FROM snapshot_boxes WHERE snapshot=?", (snapshot,))
        c.execute("UPDATE alerts SET snapshot=NULL WHERE snapshot=?", (snapshot,))
    path = config.SNAPSHOT_DIR / snapshot
    if path.exists() and ".." not in snapshot and "/" not in snapshot:
        try:
            path.unlink()
        except OSError:
            pass


# --- Cleanup / retention ---------------------------------------------------
def cleanup(retention_days):
    """Delete detections & alerts older than `retention_days`, plus any snapshot
    files no longer referenced — EXCEPT snapshots the user pinned (kept). Returns
    a summary dict. retention_days <= 0 means 'keep forever' (no-op)."""
    if not retention_days or retention_days <= 0:
        return {"skipped": True}

    import os
    import time as _time

    cutoff = _time.time() - retention_days * 86400
    with connect() as c:
        # rows older than cutoff, but never touch pinned snapshots
        det = c.execute(
            "DELETE FROM detections WHERE ts < ? AND (snapshot IS NULL OR "
            "snapshot NOT IN (SELECT snapshot FROM kept_snapshots))", (cutoff,)
        ).rowcount
        alr = c.execute(
            "DELETE FROM alerts WHERE ts < ? AND (snapshot IS NULL OR "
            "snapshot NOT IN (SELECT snapshot FROM kept_snapshots))", (cutoff,)
        ).rowcount
        # snapshots still referenced or pinned must be preserved
        referenced = set()
        for tbl in ("detections", "alerts", "kept_snapshots"):
            for r in c.execute(f"SELECT DISTINCT snapshot FROM {tbl} WHERE snapshot IS NOT NULL"):
                referenced.add(r["snapshot"])

    # remove orphaned image files on disk
    removed_files = 0
    if config.SNAPSHOT_DIR.exists():
        for f in config.SNAPSHOT_DIR.iterdir():
            if f.name not in referenced:
                try:
                    f.unlink()
                    removed_files += 1
                except OSError:
                    pass
    return {"detections_deleted": det, "alerts_deleted": alr, "files_deleted": removed_files}


def cleanup_by_count(max_snapshots):
    """Keep only the newest `max_snapshots` snapshots; delete older ones (pinned
    snapshots are exempt and don't count toward the cap). 0 = no cap. Returns the
    number of snapshots deleted."""
    if not max_snapshots or max_snapshots <= 0:
        return 0
    with connect() as c:
        snaps = c.execute(
            "SELECT snapshot, MAX(ts) AS ts FROM detections WHERE snapshot IS NOT NULL "
            "GROUP BY snapshot ORDER BY ts DESC"
        ).fetchall()
    kept = list_kept()
    unpinned = [s["snapshot"] for s in snaps if s["snapshot"] not in kept]
    over = unpinned[max_snapshots:]
    for name in over:
        delete_snapshot(name)
    return len(over)
