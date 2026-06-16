"""Inference backend for argus-pi: ONNX Runtime instead of Ultralytics/PyTorch.

This is the only file that diverges meaningfully from upstream Argus. It keeps
the exact interface the capture loop depends on, so capture.py / manager.py /
server.py are unchanged:

    Detector(model_path=None, confidence=None, device=None, track=None)
    Detector.detect(frame_bgr) -> list[{label, confidence, track_id, x1, y1, x2, y2}]
    Detector.names = {class_id: name}

The YOLO11 weights are exported to ONNX on a dev machine (see
scripts/export-onnx.sh); the Pi never needs torch or ultralytics. Tracking uses
the lightweight IoU tracker in tracker.py in place of ByteTrack. With tracking
on, each object keeps a stable id across frames so the capture loop can log it
once per appearance rather than every frame."""
import ast
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from . import config
from .tracker import IouTracker

# Fallback class names (COCO-80 in YOLO order). The Ultralytics ONNX export
# embeds the real names in model metadata, so this is only a safety net.
_COCO80 = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag",
    "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana",
    "apple", "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza",
    "donut", "cake", "chair", "couch", "potted plant", "bed", "dining table",
    "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock",
    "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
]


def resolve_device(pref):
    """The Pi 3B+ has no GPU; inference is always CPU. Kept for interface parity."""
    return "cpu"


def _letterbox(img, new_shape, color=(114, 114, 114)):
    """Resize keeping aspect ratio, pad to a square `new_shape`.

    Returns (padded_img, ratio, (pad_left, pad_top)) so detections can be mapped
    back to the original frame's coordinates."""
    h, w = img.shape[:2]
    r = min(new_shape / h, new_shape / w)
    nw, nh = int(round(w * r)), int(round(h * r))
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    dw, dh = (new_shape - nw) / 2.0, (new_shape - nh) / 2.0
    top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
    left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
    padded = cv2.copyMakeBorder(resized, top, bottom, left, right,
                                cv2.BORDER_CONSTANT, value=color)
    return padded, r, (left, top)


class Detector:
    def __init__(self, model_path=None, confidence=None, device=None, track=None):
        self.confidence = confidence if confidence is not None else config.CONFIDENCE
        self.device = "cpu"
        self.track = config.TRACK if track is None else track
        self.iou_thres = config.IOU_THRES

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = config.ORT_THREADS
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            str(self._resolve_model(model_path or config.MODEL)),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        inp = self.session.get_inputs()[0]
        self._input_name = inp.name
        # Square input fixed at export time (e.g. 320). Trust the model if it
        # reports a concrete size, else fall back to the configured IMGSZ.
        self.imgsz = inp.shape[-1] if isinstance(inp.shape[-1], int) and inp.shape[-1] > 0 \
            else config.IMGSZ

        self.names = self._load_names()
        self._tracker = (IouTracker(iou_thres=config.TRACK_IOU,
                                    max_age=config.TRACK_MAX_AGE)
                         if self.track else None)

    @staticmethod
    def _resolve_model(model):
        """Accept an absolute path, or a name/relative path resolved against the
        project root (so it works regardless of the process's cwd)."""
        p = Path(model)
        if not p.is_absolute() and not p.exists():
            cand = config.BASE_DIR / p
            if cand.exists():
                return cand
        return p

    def _load_names(self):
        meta = self.session.get_modelmeta().custom_metadata_map or {}
        raw = meta.get("names")
        if raw:
            try:
                parsed = ast.literal_eval(raw)
                if isinstance(parsed, dict):
                    return {int(k): v for k, v in parsed.items()}
                if isinstance(parsed, (list, tuple)):
                    return {i: v for i, v in enumerate(parsed)}
            except (ValueError, SyntaxError):
                pass
        return {i: n for i, n in enumerate(_COCO80)}

    def detect(self, frame):
        """Run inference on a BGR frame and return a list of detection dicts.

        track_id is filled by the IoU tracker when tracking is on, else None."""
        h0, w0 = frame.shape[:2]
        img, r, (dw, dh) = _letterbox(frame, self.imgsz)
        blob = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        blob = np.ascontiguousarray(np.transpose(blob, (2, 0, 1))[None])  # NCHW

        out = self.session.run(None, {self._input_name: blob})[0]
        # Ultralytics head: (1, 4+nc, N) -> (N, 4+nc). First 4 = cx,cy,w,h; rest = class scores.
        preds = np.squeeze(out, 0).T
        xywh = preds[:, :4]
        scores_all = preds[:, 4:]
        class_ids = np.argmax(scores_all, axis=1)
        confs = scores_all[np.arange(scores_all.shape[0]), class_ids]

        keep = confs >= self.confidence
        if not np.any(keep):
            return self._post([])
        xywh, confs, class_ids = xywh[keep], confs[keep], class_ids[keep]

        cx, cy, ww, hh = xywh.T
        x1 = cx - ww / 2.0
        y1 = cy - hh / 2.0
        # cv2.dnn.NMSBoxes wants [x, y, w, h] in letterbox space.
        nms_boxes = np.stack([x1, y1, ww, hh], axis=1).tolist()
        idxs = cv2.dnn.NMSBoxes(nms_boxes, confs.tolist(),
                                float(self.confidence), float(self.iou_thres))
        if len(idxs) == 0:
            return self._post([])
        idxs = np.array(idxs).flatten()

        detections = []
        for i in idxs:
            # Undo letterbox padding + scale back to the original frame.
            bx1 = float(np.clip((x1[i] - dw) / r, 0, w0 - 1))
            by1 = float(np.clip((y1[i] - dh) / r, 0, h0 - 1))
            bx2 = float(np.clip((x1[i] + ww[i] - dw) / r, 0, w0 - 1))
            by2 = float(np.clip((y1[i] + hh[i] - dh) / r, 0, h0 - 1))
            detections.append({
                "label": self.names.get(int(class_ids[i]), str(int(class_ids[i]))),
                "confidence": float(confs[i]),
                "track_id": None,
                "x1": bx1, "y1": by1, "x2": bx2, "y2": by2,
            })
        return self._post(detections)

    def _post(self, detections):
        if self._tracker is not None:
            return self._tracker.update(detections)
        return detections
