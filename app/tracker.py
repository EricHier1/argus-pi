"""Lightweight IoU tracker — a cheap stand-in for ByteTrack on the Pi.

Assigns a stable integer id to each detection by greedily matching new boxes to
existing tracks of the same label by IoU. A track that goes unmatched for
`max_age` consecutive updates expires, so its id can be recycled. One IouTracker
instance lives per camera (created by Detector when tracking is enabled).

This is intentionally simple: no motion model, no Kalman filter. It is enough to
give the capture loop a stable id per object appearance — which is all it needs
to log an object once per appearance instead of every frame — at a fraction of
ByteTrack's cost. Occlusions/crossings may swap ids; for single-camera
surveillance that is an acceptable trade for running on a Cortex-A53."""


def _iou(a, b):
    """Intersection-over-union of two (x1, y1, x2, y2) boxes."""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


class IouTracker:
    def __init__(self, iou_thres=0.3, max_age=30):
        self.iou_thres = iou_thres
        self.max_age = max_age
        self._next_id = 1
        self._tracks = {}   # id -> {"box": (x1,y1,x2,y2), "label": str, "missed": int}

    def update(self, detections):
        """Assign `track_id` to each detection (a list of dicts with label + box).

        Mutates each dict in place and returns the same list. Detections are
        matched highest-confidence first so the strongest box claims a track."""
        # Age every existing track; matched ones get reset to 0 below.
        for t in self._tracks.values():
            t["missed"] += 1

        used = set()
        order = sorted(range(len(detections)),
                       key=lambda i: detections[i].get("confidence", 0.0),
                       reverse=True)
        for i in order:
            d = detections[i]
            box = (d["x1"], d["y1"], d["x2"], d["y2"])
            best_id, best_iou = None, self.iou_thres
            for tid, t in self._tracks.items():
                if tid in used or t["label"] != d["label"]:
                    continue
                score = _iou(box, t["box"])
                if score >= best_iou:
                    best_id, best_iou = tid, score
            if best_id is None:
                best_id = self._next_id
                self._next_id += 1
                self._tracks[best_id] = {"box": box, "label": d["label"], "missed": 0}
            else:
                self._tracks[best_id]["box"] = box
                self._tracks[best_id]["missed"] = 0
            used.add(best_id)
            d["track_id"] = best_id

        # Drop tracks that have gone unseen past max_age.
        for tid in [t for t, v in self._tracks.items() if v["missed"] > self.max_age]:
            self._tracks.pop(tid, None)
        return detections
