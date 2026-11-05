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

DETECTOR_SCALES (e.g. "1280,1536") runs the model at each input size, and
DETECTOR_WEIGHTS may list several comma-separated models (an ensemble; each
runs at every scale). The passes are merged with weighted box fusion: boxes of
the same class that overlap (IoU >= 0.55) become one box, with the
score-weighted mean position and the mean score over passes (0 for a pass that
missed it). A request that names imgsz runs that single size.
"""
import base64
import binascii
import io
import logging
import os
import threading
import time
import urllib.request

import torch
from fastapi import FastAPI, Header, HTTPException
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from ultralytics import YOLO

from fusion import wbf

log = logging.getLogger("detector")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def env_list(name: str, default: str = "") -> list[str]:
    return [s.strip() for s in os.environ.get(name, default).split(",") if s.strip()]


WEIGHTS = env_list("DETECTOR_WEIGHTS", "/models/visdrone.pt")
WEIGHTS_URLS = env_list("DETECTOR_WEIGHTS_URL")  # aligned with DETECTOR_WEIGHTS
MODEL_NAME = os.environ.get("DETECTOR_MODEL_NAME") or "+".join(os.path.splitext(os.path.basename(w))[0] for w in WEIGHTS)
IMGSZ = int(os.environ.get("DETECTOR_IMGSZ", "1280"))
SCALES = [int(s) for s in os.environ.get("DETECTOR_SCALES", "").replace(" ", "").split(",") if s] or [IMGSZ]
CONF = float(os.environ.get("DETECTOR_CONF", "0.3"))
MAX_DET = int(os.environ.get("DETECTOR_MAX_DET", "1000"))
DEVICE = os.environ.get("DETECTOR_DEVICE") or ("cuda:0" if torch.cuda.is_available() else "cpu")
API_KEY = os.environ.get("DETECTOR_API_KEY", "")
MAX_PIXELS = 50_000_000


def is_lfs_pointer(path: str) -> bool:
    with open(path, "rb") as f:
        return f.read(64).startswith(b"version https://git-lfs")


def ensure_weights(path: str, url: str) -> str:
    """Return a local path holding the weights, downloading them if needed."""
    if os.path.exists(path) and is_lfs_pointer(path):
        if not url:
            raise SystemExit(f"{path} is a Git LFS pointer, not the weights: run `git lfs pull` in the repository")
        log.warning("%s is a Git LFS pointer; downloading the weights from DETECTOR_WEIGHTS_URL instead", path)
        path = os.path.join("/tmp", os.path.basename(path))  # the repository mount is read-only
    if os.path.exists(path):
        return path
    if not url:
        raise SystemExit(f"weights {path} not found and DETECTOR_WEIGHTS_URL is not set")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    log.info("downloading weights from %s", url)
    urllib.request.urlretrieve(url, path + ".part")
    os.replace(path + ".part", path)
    return path


models = [YOLO(ensure_weights(w, WEIGHTS_URLS[i] if i < len(WEIGHTS_URLS) else ""))
          for i, w in enumerate(WEIGHTS)]
# The first model's classes are the vocabulary; other models are fused by
# class name and their extra classes (e.g. VisDrone "others") are dropped.
CLASSES = list(models[0].names.values())
if any(not set(CLASSES) <= set(m.names.values()) for m in models[1:]):
    raise SystemExit(f"every model in DETECTOR_WEIGHTS must detect {CLASSES}")
lock = threading.Lock()  # one inference at a time per process (GPU memory, CPU threads)
with lock:
    for m in models:
        for s in SCALES:
            m.predict(Image.new("RGB", (640, 640)), imgsz=s, device=DEVICE, verbose=False)
log.info("model %s (%d weights) on %s, imgsz %s, conf %.2f, classes %s",
         MODEL_NAME, len(models), DEVICE, SCALES, CONF, CLASSES)

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


def run(model: YOLO, img: Image.Image, imgsz: int, conf: float, max_det: int) -> list[tuple[str, list[float], float]]:
    r = model.predict(img, imgsz=imgsz, conf=conf, iou=0.6, max_det=max_det,
                      device=DEVICE, half=DEVICE.startswith("cuda"), verbose=False)[0]
    b = r.boxes
    out = [(model.names[int(c)], xyxy, float(s)) for c, xyxy, s in zip(b.cls.tolist(), b.xyxy.tolist(), b.conf.tolist())]
    return [d for d in out if d[0] in CLASSES]


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME, "weights": [os.path.basename(w) for w in WEIGHTS], "device": DEVICE,
            "imgsz": SCALES[0], "scales": SCALES, "conf": CONF, "classes": CLASSES}


@app.post("/v1/detect")
def detect(req: DetectRequest, authorization: str | None = Header(None)):
    check_key(authorization)
    img = decode(req.image)
    conf = req.conf or CONF
    scales = [req.imgsz] if req.imgsz else SCALES
    max_det = req.max_det or MAX_DET
    start = time.perf_counter()
    with lock:
        if len(scales) == 1 and len(models) == 1:
            dets = run(models[0], img, scales[0], conf, max_det)
        else:
            # Fused scores average over passes, so keep low-scoring boxes
            # until after fusion.
            passes = [run(m, img, s, min(conf, 0.05), max_det) for m in models for s in scales]
            dets = sorted((d for d in wbf(passes) if d[2] >= conf), key=lambda d: -d[2])[:max_det]
    objects = [{"label": label, "score": round(s, 4), "bbox": [round(float(v), 1) for v in xyxy]}
               for label, xyxy, s in dets]
    ms = (time.perf_counter() - start) * 1000
    return {"model": MODEL_NAME, "width": img.width, "height": img.height, "conf": conf,
            "imgsz": scales[0], "scales": scales, "latency_ms": round(ms, 1), "objects": objects}
