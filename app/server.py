"""FastAPI app: live MJPEG stream, detection search, alert rules, snapshots."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query, Response
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import capture, config, db, devices, logfilter, maintenance, onvif
from .manager import manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    logfilter.install()   # quiet the high-frequency polling endpoints in the access log
    db.init()
    manager.start()
    # honor the remembered detection on/off state across restarts
    if db.get_setting("paused", "0") == "1":
        manager.pause_all()
    maintenance.start()
    yield
    maintenance.stop()
    manager.stop_all()


app = FastAPI(title="Argus", lifespan=lifespan)


@app.middleware("http")
async def no_cache_assets(request, call_next):
    """Make the browser revalidate the HTML/JS/CSS every load, so UI changes show
    up on a normal reload (no hard-refresh needed)."""
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or p.endswith((".html", ".js", ".css")):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


# --- live feed -------------------------------------------------------------
@app.get("/stream")
def stream(source: str = Query(None)):
    """MJPEG stream for one camera (defaults to the primary camera)."""
    w = manager.get(source)
    if w is None:
        return JSONResponse({"error": "no such camera"}, status_code=404)
    return StreamingResponse(
        w.mjpeg_frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.get("/frame")
def frame(source: str = Query(None)):
    """Latest single JPEG frame for a camera. The web UI polls this for the live
    view — far more reliable than MJPEG across browsers (esp. iOS Safari)."""
    w = manager.get(source)
    if w is None:
        return JSONResponse({"error": "no such camera"}, status_code=404)
    jpeg = w.get_jpeg()
    if jpeg is None:
        return Response(status_code=204)   # no frame yet / paused — keep last image
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/status")
def status():
    """Status for every active camera (with custom names), plus the box toggle."""
    names = db.get_camera_names()
    cams = []
    for s in manager.statuses():
        c = dict(s)
        c["name"] = names.get(str(c["source"]))
        cams.append(c)
    return {"cameras": cams, "show_boxes": capture.get_show_boxes()}


@app.post("/api/boxes")
def set_boxes(payload: dict):
    """Toggle bounding boxes on the live feed. `on`: true|false."""
    capture.set_show_boxes(bool(payload.get("on", True)))
    return {"show_boxes": capture.get_show_boxes()}


@app.post("/api/power")
def power(payload: dict):
    """Turn detection on/off to save compute. `on`: true|false. Optional `source`
    targets one camera; omitted = all cameras."""
    on = bool(payload.get("on"))
    source = payload.get("source")
    if source is not None:
        w = manager.get(str(source))
        if w is None:
            return JSONResponse({"error": "no such camera"}, status_code=404)
        w.resume() if on else w.pause()
    else:
        manager.resume_all() if on else manager.pause_all()
        db.set_setting("paused", "0" if on else "1")   # remember across restarts
    return {"cameras": manager.statuses()}


# --- camera management -----------------------------------------------------
@app.get("/api/devices")
def list_devices():
    """Detected cameras on this machine, the sources in use, and custom names."""
    return {"devices": devices.list_cameras(), "active": manager.sources(),
            "names": db.get_camera_names()}


@app.get("/api/discover")
def discover_cameras():
    """Find ONVIF network (IP) cameras via WS-Discovery (same network segment)."""
    return {"found": onvif.discover()}


@app.post("/api/cameras/probe")
def probe_camera(payload: dict):
    """Ask an IP camera (by address) for its RTSP stream URL(s) via ONVIF.
    Works across subnets. `ip` required; `user`/`password`/`port` optional."""
    ip = str(payload.get("ip", "")).strip()
    if not ip:
        return JSONResponse({"error": "ip required"}, status_code=400)
    urls = onvif.probe_ip(ip, payload.get("user", ""), payload.get("password", ""),
                          int(payload.get("port", 80)))
    return {"urls": urls}


@app.post("/api/cameras")
def add_camera(payload: dict):
    """Add a camera. `source` = a camera index ('0','1',…) or a stream URL."""
    source = str(payload.get("source", "")).strip()
    if not source:
        return JSONResponse({"error": "source is required"}, status_code=400)
    manager.add(source)
    return {"ok": True, "active": manager.sources()}


@app.post("/api/cameras/remove")
def remove_camera(payload: dict):
    """Remove (stop) a camera by its source string."""
    source = str(payload.get("source", "")).strip()
    removed = manager.remove(source)
    return {"ok": removed, "active": manager.sources()}


@app.post("/api/cameras/name")
def name_camera(payload: dict):
    """Set (or clear, if blank) a custom display name for a camera source."""
    source = str(payload.get("source", "")).strip()
    if not source:
        return JSONResponse({"error": "source required"}, status_code=400)
    db.set_camera_name(source, payload.get("name", ""))
    return {"ok": True, "names": db.get_camera_names()}


@app.get("/api/activity")
def activity(hours: int = Query(24, ge=1, le=168), buckets: int = Query(24, ge=4, le=96)):
    """Detection counts over time, for the dashboard timeline chart."""
    return db.activity(hours=hours, buckets=buckets)


@app.post("/api/shutdown")
def shutdown():
    """Cleanly stop all cameras and exit the process. Reliable where Ctrl-C isn't
    (the live MJPEG streams block uvicorn's graceful shutdown, so we force-exit
    after releasing the cameras)."""
    import os
    import threading

    def _kill():
        try:
            maintenance.stop()
            manager.stop_all()
        finally:
            os._exit(0)

    threading.Timer(0.3, _kill).start()  # let this HTTP response flush first
    return {"ok": True, "message": "Argus is shutting down."}


# --- detections & search ---------------------------------------------------
@app.get("/api/detections")
def detections(label: str = Query(None), start: float = Query(None),
               end: float = Query(None), limit: int = Query(200, le=1000)):
    return db.search_detections(label=label or None, start=start, end=end, limit=limit)


@app.get("/api/labels")
def labels():
    return db.label_summary()


@app.get("/api/gallery")
def gallery(label: str = Query(None), limit: int = Query(30, le=200),
            pinned: bool = Query(False), before: float = Query(None)):
    """Gallery grouped by object (each item is one object appearance), paginated
    by the `before` timestamp cursor."""
    return db.gallery(label=label or None, limit=limit, pinned=pinned, before=before)


@app.get("/api/gallery/group")
def gallery_group(gkey: str = Query(...)):
    """All snapshots for one object group, for the expanded (stacked) view."""
    return db.gallery_group(gkey)


@app.get("/api/snapshot-boxes")
def snapshot_boxes(name: str = Query(...)):
    """Box geometry for a snapshot, for the toggleable gallery overlay."""
    return db.get_snapshot_boxes(name)


@app.delete("/api/gallery/group")
def delete_gallery_group(gkey: str = Query(...)):
    """Delete all snapshots belonging to one object group."""
    return {"ok": True, "deleted": db.delete_group(gkey)}


@app.delete("/api/gallery/all")
def delete_all_gallery():
    """Delete the entire gallery (all detections + their snapshots)."""
    return db.delete_all_gallery()


@app.delete("/api/alerts/all")
def delete_all_alerts():
    """Delete every alert."""
    return db.delete_all_alerts()


# --- storage settings, pinning, cleanup ------------------------------------
@app.get("/api/settings")
def get_settings():
    return {
        "retention_days": int(float(db.get_setting("retention_days", "0"))),
        "max_snapshots": int(float(db.get_setting("max_snapshots", "0"))),
    }


@app.post("/api/settings")
def update_settings(payload: dict):
    if "retention_days" in payload:
        db.set_setting("retention_days", max(0, int(payload["retention_days"])))
    if "max_snapshots" in payload:
        db.set_setting("max_snapshots", max(0, int(payload["max_snapshots"])))
    return get_settings()


@app.post("/api/keep")
def keep(payload: dict):
    """Pin/unpin a snapshot so cleanup never deletes it. `snapshot`, `kept` bool."""
    snapshot = str(payload.get("snapshot", "")).strip()
    if not snapshot:
        return JSONResponse({"error": "snapshot required"}, status_code=400)
    db.set_kept(snapshot, bool(payload.get("kept", True)), payload.get("note"))
    return {"snapshot": snapshot, "kept": bool(payload.get("kept", True))}


@app.post("/api/cleanup")
def cleanup_now():
    """Run a cleanup pass immediately using the current retention setting."""
    return maintenance.run_once()


@app.delete("/api/snapshot/{name}")
def delete_snapshot(name: str):
    """Permanently delete one snapshot and its detection records."""
    if ".." in name or "/" in name:
        return JSONResponse({"error": "bad name"}, status_code=400)
    db.delete_snapshot(name)
    return {"ok": True, "deleted": name}


# --- alert rules -----------------------------------------------------------
@app.get("/api/rules")
def get_rules():
    return db.list_rules()


@app.post("/api/rules")
def create_rule(payload: dict):
    rule_id = db.add_rule(
        label=payload["label"],
        min_conf=float(payload.get("min_conf", 0.5)),
        start_hour=payload.get("start_hour"),
        end_hour=payload.get("end_hour"),
    )
    return {"id": rule_id}


@app.post("/api/rules/{rule_id}/toggle")
def toggle_rule(rule_id: int, payload: dict):
    db.set_rule_active(rule_id, bool(payload.get("active", True)))
    return {"ok": True}


@app.delete("/api/rules/{rule_id}")
def remove_rule(rule_id: int):
    db.delete_rule(rule_id)
    return {"ok": True}


# --- alerts ----------------------------------------------------------------
@app.get("/api/alerts")
def alerts_feed(since: float = Query(None), limit: int = Query(100, le=500),
                before: float = Query(None)):
    return db.recent_alerts(limit=limit, since=since, before=before)


@app.get("/api/alerts/count")
def alerts_count():
    return {"count": db.count_alerts()}


@app.get("/api/analytics")
def analytics():
    return db.analytics()


# --- snapshots & static web ------------------------------------------------
@app.get("/snapshots/{name}")
def snapshot(name: str, download: bool = Query(False)):
    path = config.SNAPSHOT_DIR / name
    if not path.exists() or ".." in name or "/" in name:
        return JSONResponse({"error": "not found"}, status_code=404)
    if download:
        return FileResponse(path, media_type="image/jpeg", filename=f"argus-{name}")
    return FileResponse(path)


app.mount("/", StaticFiles(directory=str(config.WEB_DIR), html=True), name="web")
