"""Multimo object detector.

A small HTTP service around an Ultralytics YOLO model trained on VisDrone
(pedestrian, people, bicycle, car, van, truck, tricycle, awning-tricycle, bus,
motor). The backend calls it for drone photos and video keyframes; the VLM
still writes descriptions and handles everything outside this vocabulary.

    GET  /health      model, device, classes
    POST /v1/detect   {"image": "<base64 or data URL>", "conf": 0.3, "imgsz": 1280}
                      -> {"objects": [{"label", "score", "bbox": [x1, y1, x2, y2]}], ...}

Boxes are in pixels of the image that was sent. Set DETECTOR_API_KEY to
require "Authorization: Bearer <key>".

DETECTOR_SCALES (e.g. "1280,1536") runs the model at each input size and
merges the results with weighted box fusion: boxes of the same class that
overlap (IoU >= 0.55) across scales become one box, with the score-weighted
mean position and the mean score over scales (0 for a scale that missed it).
A request that names imgsz runs that single size.
"""
import base64
import binascii
import io
import logging
import os
import threading
import time
import urllib.request

import numpy as np
import torch
from fastapi import FastAPI, Header, HTTPException
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from ultralytics import YOLO

log = logging.getLogger("detector")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

WEIGHTS = os.environ.get("DETECTOR_WEIGHTS", "/models/visdrone.pt")
WEIGHTS_URL = os.environ.get("DETECTOR_WEIGHTS_URL", "")
MODEL_NAME = os.environ.get("DETECTOR_MODEL_NAME") or os.path.splitext(os.path.basename(WEIGHTS))[0]
IMGSZ = int(os.environ.get("DETECTOR_IMGSZ", "1280"))
SCALES = [int(s) for s in os.environ.get("DETECTOR_SCALES", "").replace(" ", "").split(",") if s] or [IMGSZ]
CONF = float(os.environ.get("DETECTOR_CONF", "0.3"))
MAX_DET = int(os.environ.get("DETECTOR_MAX_DET", "1000"))
DEVICE = os.environ.get("DETECTOR_DEVICE") or ("cuda:0" if torch.cuda.is_available() else "cpu")
API_KEY = os.environ.get("DETECTOR_API_KEY", "")
MAX_PIXELS = 50_000_000


def ensure_weights() -> None:
    if os.path.exists(WEIGHTS):
        return
    if not WEIGHTS_URL:
        raise SystemExit(f"weights {WEIGHTS} not found and DETECTOR_WEIGHTS_URL is not set")
    os.makedirs(os.path.dirname(WEIGHTS) or ".", exist_ok=True)
    log.info("downloading weights from %s", WEIGHTS_URL)
    tmp = WEIGHTS + ".part"
    urllib.request.urlretrieve(WEIGHTS_URL, tmp)
    os.replace(tmp, WEIGHTS)


ensure_weights()
model = YOLO(WEIGHTS)
names = model.names
lock = threading.Lock()  # one inference at a time per process (GPU memory, CPU threads)
with lock:
    for s in SCALES:
        model.predict(Image.new("RGB", (640, 640)), imgsz=s, device=DEVICE, verbose=False)
log.info("model %s on %s, imgsz %s, conf %.2f, classes %s", MODEL_NAME, DEVICE, SCALES, CONF, list(names.values()))

app = FastAPI(title="Multimo detector", version="1.0")


class DetectRequest(BaseModel):
    image: str = Field(description="base64 JPEG/PNG, optionally as a data URL")
    conf: float | None = Field(None, ge=0.01, le=0.99)
    imgsz: int | None = Field(None, ge=320, le=2560)
    max_det: int | None = Field(None, ge=1, le=3000)


def check_key(authorization: str | None) -> None:
    if API_KEY and authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="invalid api key")


def decode(data: str) -> Image.Image:
    if data.startswith("data:"):
        data = data.split(",", 1)[-1]
    try:
        img = Image.open(io.BytesIO(base64.b64decode(data, validate=False)))
        if img.width * img.height > MAX_PIXELS:
            raise HTTPException(status_code=413, detail="image too large")
        return img.convert("RGB")
    except (binascii.Error, UnidentifiedImageError, OSError) as e:
        raise HTTPException(status_code=400, detail=f"cannot decode image: {e}") from e


def run(img: Image.Image, imgsz: int, conf: float, max_det: int) -> list[tuple[int, list[float], float]]:
    r = model.predict(img, imgsz=imgsz, conf=conf, iou=0.6, max_det=max_det,
                      device=DEVICE, half=DEVICE.startswith("cuda"), verbose=False)[0]
    b = r.boxes
    return [(int(c), xyxy, float(s)) for c, xyxy, s in zip(b.cls.tolist(), b.xyxy.tolist(), b.conf.tolist())]


def fuse(per_scale: list[list[tuple[int, list[float], float]]], iou_thr: float = 0.55):
    """Weighted box fusion across scales (see the module docstring)."""
    k = len(per_scale)
    out = []
    dets = sorted(((c, np.array(b), s, m) for m, ds in enumerate(per_scale) for c, b, s in ds), key=lambda d: -d[2])
    for cls in {d[0] for d in dets}:
        sums, weights, scores, boxes = [], [], [], np.zeros((0, 4))
        for _, b, s, m in (d for d in dets if d[0] == cls):
            if len(boxes):
                ix = np.clip(np.minimum(b[2], boxes[:, 2]) - np.maximum(b[0], boxes[:, 0]), 0, None)
                iy = np.clip(np.minimum(b[3], boxes[:, 3]) - np.maximum(b[1], boxes[:, 1]), 0, None)
                inter = ix * iy
                union = (b[2] - b[0]) * (b[3] - b[1]) + (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1]) - inter
                ious = inter / np.maximum(union, 1e-9)
                j = int(ious.argmax())
                if ious[j] >= iou_thr:
                    sums[j] += s * b
                    weights[j] += s
                    scores[j][m] = max(scores[j][m], s)
                    boxes[j] = sums[j] / weights[j]
                    continue
            sums.append(s * b)
            weights.append(s)
            scores.append([0.0] * k)
            scores[-1][m] = s
            boxes = np.vstack([boxes, b])
        out += [(cls, box.tolist(), sum(sc) / k) for box, sc in zip(boxes, scores)]
    return out


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME, "device": DEVICE, "imgsz": SCALES[0], "scales": SCALES,
            "conf": CONF, "classes": list(names.values())}


@app.post("/v1/detect")
def detect(req: DetectRequest, authorization: str | None = Header(None)):
    check_key(authorization)
    img = decode(req.image)
    conf = req.conf or CONF
    scales = [req.imgsz] if req.imgsz else SCALES
    max_det = req.max_det or MAX_DET
    start = time.perf_counter()
    with lock:
        if len(scales) == 1:
            dets = run(img, scales[0], conf, max_det)
        else:
            # Fused scores average over scales, so keep low-scoring boxes
            # until after fusion.
            dets = [d for d in fuse([run(img, s, min(conf, 0.05), max_det) for s in scales]) if d[2] >= conf]
            dets = sorted(dets, key=lambda d: -d[2])[:max_det]
    objects = [{"label": names[c], "score": round(s, 4), "bbox": [round(float(v), 1) for v in xyxy]}
               for c, xyxy, s in dets]
    ms = (time.perf_counter() - start) * 1000
    return {"model": MODEL_NAME, "width": img.width, "height": img.height, "conf": conf,
            "imgsz": scales[0], "scales": scales, "latency_ms": round(ms, 1), "objects": objects}
