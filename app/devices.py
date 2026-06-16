"""Enumerate available video input devices, cross-platform.

- macOS: `system_profiler` (no camera is opened, so it won't fight the running
  capture thread). Order maps to OpenCV's AVFoundation indices 0, 1, 2…
- Linux (incl. Raspberry Pi / RHEL / Rocky): read /dev/video* and their names
  from /sys/class/video4linux. The N in videoN maps to OpenCV's V4L2 index.
- Other platforms: empty list (the UI falls back to manual index/URL entry).
"""
import glob
import json
import os
import subprocess
import sys

# Raspberry Pi (and similar SoCs) expose the GPU's hardware video codec and ISP
# as /dev/video* memory-to-memory nodes — e.g. bcm2835-codec-decode/encode,
# bcm2835-isp-*. These are NOT capture cameras; skip them by driver name so the
# UI's camera list shows only real inputs.
_NON_CAPTURE_PREFIXES = ("bcm2835", "rpivid", "pispbe", "rp1-vec", "codec")


def list_cameras():
    """Return [{'index': int, 'name': str}, …]. Empty list on any failure."""
    try:
        if sys.platform == "darwin":
            return _macos()
        if sys.platform.startswith("linux"):
            return _linux()
    except Exception:
        pass
    return []


def _macos():
    out = subprocess.run(
        ["system_profiler", "SPCameraDataType", "-json"],
        capture_output=True, text=True, timeout=8,
    )
    data = json.loads(out.stdout or "{}")
    cams = data.get("SPCameraDataType", [])
    return [{"index": i, "name": c.get("_name", f"Camera {i}")}
            for i, c in enumerate(cams)]


def _linux():
    cams = []
    seen_devs = set()
    for path in sorted(glob.glob("/dev/video*"),
                       key=lambda p: int("".join(filter(str.isdigit, p)) or 0)):
        digits = "".join(filter(str.isdigit, path))
        if not digits:
            continue
        idx = int(digits)
        name = f"Camera {idx}"
        try:
            with open(f"/sys/class/video4linux/video{idx}/name") as f:
                name = f.read().strip() or name
        except OSError:
            pass
        # Skip the SoC's codec/ISP M2M nodes (bcm2835-*, etc.) — not cameras.
        if name.lower().startswith(_NON_CAPTURE_PREFIXES):
            continue
        # A physical camera exposes several /dev/video nodes (capture + metadata)
        # that share one USB device path; collapse them to the first (capture)
        # node. Dedup by device path, NOT name, so two *identical* cameras (same
        # name, different USB port) are both listed.
        dev = os.path.realpath(f"/sys/class/video4linux/video{idx}/device")
        if dev in seen_devs:
            continue
        seen_devs.add(dev)
        cams.append({"index": idx, "name": name})
    # Disambiguate identical names (e.g. two identical USB cameras) by appending
    # the node, so they're tellable apart in the UI until renamed.
    counts = {}
    for c in cams:
        counts[c["name"]] = counts.get(c["name"], 0) + 1
    for c in cams:
        if counts[c["name"]] > 1:
            c["name"] = f'{c["name"]} (video{c["index"]})'
    return cams
