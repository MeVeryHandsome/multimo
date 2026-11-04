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

log = logging.getLogger("detector")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

WEIGHTS = os.environ.get("DETECTOR_WEIGHTS", "/models/visdrone.pt")
WEIGHTS_URL = os.environ.get("DETECTOR_WEIGHTS_URL", "")
MODEL_NAME = os.environ.get("DETECTOR_MODEL_NAME") or os.path.splitext(os.path.basename(WEIGHTS))[0]
IMGSZ = int(os.environ.get("DETECTOR_IMGSZ", "1280"))
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
    model.predict(Image.new("RGB", (640, 640)), imgsz=IMGSZ, device=DEVICE, verbose=False)
log.info("model %s on %s, imgsz %d, conf %.2f, classes %s", MODEL_NAME, DEVICE, IMGSZ, CONF, list(names.values()))

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


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME, "device": DEVICE, "imgsz": IMGSZ, "conf": CONF,
            "classes": list(names.values())}


@app.post("/v1/detect")
def detect(req: DetectRequest, authorization: str | None = Header(None)):
    check_key(authorization)
    img = decode(req.image)
    conf, imgsz = req.conf or CONF, req.imgsz or IMGSZ
    start = time.perf_counter()
    with lock:
        r = model.predict(img, imgsz=imgsz, conf=conf, iou=0.6, max_det=req.max_det or MAX_DET,
                          device=DEVICE, half=DEVICE.startswith("cuda"), verbose=False)[0]
    b = r.boxes
    objects = [{"label": names[int(c)], "score": round(float(s), 4), "bbox": [round(float(v), 1) for v in xyxy]}
               for c, s, xyxy in zip(b.cls.tolist(), b.conf.tolist(), b.xyxy.tolist())]
    ms = (time.perf_counter() - start) * 1000
    return {"model": MODEL_NAME, "width": img.width, "height": img.height, "conf": conf, "imgsz": imgsz,
            "latency_ms": round(ms, 1), "objects": objects}
