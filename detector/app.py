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

DETECTOR_FLIP=1 adds a horizontally mirrored pass for every model and scale.
DETECTOR_RESCORER points to a rescorer (see rescore.py) trained for exactly
this set of passes; the fused boxes are then re-scored by it and `conf`
applies to the rescored probability. Pass order: every model (in
DETECTOR_WEIGHTS order) at every scale, then the mirrored passes in the same
order.
"""
import base64
import binascii
import io
import logging
import os
import pickle
import threading
import time
import urllib.request

import torch
from fastapi import FastAPI, Header, HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field
from ultralytics import YOLO

import rescore
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
FLIP = os.environ.get("DETECTOR_FLIP", "").lower() in ("1", "true", "yes")
RESCORER_PATH = os.environ.get("DETECTOR_RESCORER", "")
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
RESCORER = None
if RESCORER_PATH:
    with open(ensure_weights(RESCORER_PATH, ""), "rb") as f:
        RESCORER = pickle.load(f)  # our own file, shipped next to the weights
    n_passes = len(models) * len(SCALES) * (2 if FLIP else 1)
    if RESCORER["passes"] != n_passes:
        raise SystemExit(f"{RESCORER_PATH} was trained for {RESCORER['passes']} passes, "
                         f"but DETECTOR_WEIGHTS x DETECTOR_SCALES x DETECTOR_FLIP give {n_passes}")
lock = threading.Lock()  # one inference at a time per process (GPU memory, CPU threads)
with lock:
    for m in models:
        for s in SCALES:
            m.predict(Image.new("RGB", (640, 640)), imgsz=s, device=DEVICE, verbose=False)
log.info("model %s (%d weights) on %s, imgsz %s, flip %s, rescorer %s, conf %.2f, classes %s",
         MODEL_NAME, len(models), DEVICE, SCALES, FLIP, RESCORER_PATH or "none", CONF, CLASSES)

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


def run(model: YOLO, img: Image.Image, imgsz: int, conf: float, max_det: int,
        mirrored: bool = False) -> list[tuple[str, list[float], float]]:
    if mirrored:
        img = ImageOps.mirror(img)
    r = model.predict(img, imgsz=imgsz, conf=conf, iou=0.6, max_det=max_det,
                      device=DEVICE, half=DEVICE.startswith("cuda"), verbose=False)[0]
    b = r.boxes
    out = []
    for c, (x1, y1, x2, y2), s in zip(b.cls.tolist(), b.xyxy.tolist(), b.conf.tolist()):
        label = model.names[int(c)]
        if label in CLASSES:
            box = [img.width - x2, y1, img.width - x1, y2] if mirrored else [x1, y1, x2, y2]
            out.append((label, box, float(s)))
    return out


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME, "weights": [os.path.basename(w) for w in WEIGHTS], "device": DEVICE,
            "imgsz": SCALES[0], "scales": SCALES, "flip": FLIP,
            "rescorer": os.path.basename(RESCORER_PATH) if RESCORER else None, "conf": CONF, "classes": CLASSES}


@app.post("/v1/detect")
def detect(req: DetectRequest, authorization: str | None = Header(None)):
    check_key(authorization)
    img = decode(req.image)
    conf = req.conf or CONF
    scales = [req.imgsz] if req.imgsz else SCALES
    max_det = req.max_det or MAX_DET
    start = time.perf_counter()
    flips = [False, True] if FLIP and not req.imgsz else [False]
    rescorer = RESCORER if not req.imgsz else None  # a single-size request skips the rescorer
    with lock:
        if len(scales) == 1 and len(models) == 1 and len(flips) == 1 and rescorer is None:
            dets = run(models[0], img, scales[0], conf, max_det)
        else:
            # Fused (and rescored) scores are computed from all passes, so
            # keep low-scoring boxes until after fusion.
            passes = [run(m, img, s, 0.05, max_det, mirrored=f) for f in flips for m in models for s in scales]
            fused = rescore.apply(rescorer, passes, img.width, img.height) if rescorer else wbf(passes)
            dets = sorted((d for d in fused if d[2] >= conf), key=lambda d: -d[2])[:max_det]
    objects = [{"label": label, "score": round(s, 4), "bbox": [round(float(v), 1) for v in xyxy]}
               for label, xyxy, s in dets]
    ms = (time.perf_counter() - start) * 1000
    return {"model": MODEL_NAME, "width": img.width, "height": img.height, "conf": conf,
            "imgsz": scales[0], "scales": scales, "latency_ms": round(ms, 1), "objects": objects}
