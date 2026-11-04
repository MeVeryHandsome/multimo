"""Evaluate a VisDrone detector with the same protocol as the backend testsuite
(IoU >= 0.5, same class, predictions on hidden objects / ignored regions not
counted), on the benchmark's 100 VisDrone-DET val images or (SPLIT=calib) on
the other 448 val images, which are used to choose the confidence threshold.

    TESTDATA_DIR=../../testdata python eval_visdrone.py weights.pt 1024 0.5 0.55 0.6
    SPLIT=calib TESTDATA_DIR=../../testdata python eval_visdrone.py weights.pt 1024 0.55 0.56 0.57

Predictions are cached per (weights, imgsz, split) at conf 0.05 in ./preds, so
thresholds can be swept without re-running the model."""
import json, os, sys, time
import numpy as np

TD = os.environ.get("TESTDATA_DIR", "../../testdata")
HERE = os.path.dirname(os.path.abspath(__file__))
CLASSES = {1: "pedestrian", 2: "people", 3: "bicycle", 4: "car", 5: "van", 6: "truck", 7: "tricycle",
           8: "awning-tricycle", 9: "bus", 10: "motor"}
GROUP = {"car": "v", "van": "v", "truck": "v", "bus": "v", "pedestrian": "p", "people": "p",
         "bicycle": "b", "motor": "b", "tricycle": "t", "awning-tricycle": "t"}


def gt_objects(path):
    out = []
    for line in open(path):
        v = [int(x) for x in line.strip().rstrip(",").split(",")[:8]]
        x, y, w, h, cat, occ = v[0], v[1], v[2], v[3], v[5], v[7]
        b = (x, y, x + w, y + h)
        if cat not in CLASSES:
            out.append(("ignored", b, False, True))
        else:
            out.append((CLASSES[cat], b, w * h >= 600 and occ < 2, False))
    return out


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0


def inside(a, r):
    ix = max(0, min(a[2], r[2]) - max(a[0], r[0])); iy = max(0, min(a[3], r[3]) - max(a[1], r[1]))
    area = (a[2] - a[0]) * (a[3] - a[1])
    return ix * iy / area if area > 0 else 0


def match(preds, gt, compat):
    pairs = []
    for i, (pl, pb) in enumerate(preds):
        for j, (gl, gb, vis, reg) in enumerate(gt):
            if vis and not reg and compat(pl, gl):
                v = iou(pb, gb)
                if v >= 0.5:
                    pairs.append((v, i, j))
    pairs.sort(reverse=True)
    up, ug = set(), set()
    tp = fp = ign = 0
    for v, i, j in pairs:
        if i in up or j in ug:
            continue
        up.add(i); ug.add(j); tp += 1
    for i, (pl, pb) in enumerate(preds):
        if i in up:
            continue
        if any((reg and inside(pb, gb) >= 0.5) or (not vis and not reg and iou(pb, gb) >= 0.5) for _, gb, vis, reg in gt):
            ign += 1
        else:
            fp += 1
    fn = sum(1 for j, (_, _, vis, reg) in enumerate(gt) if vis and not reg and j not in ug)
    return tp, fp, fn, ign


def predict(weights, imgsz, items, extra=None, split="bench"):
    tag = os.path.basename(os.path.dirname(weights)) + f"_{imgsz}" + (f"_{extra}" if extra else "") + ("" if split == "bench" else "_" + split)
    cache = os.path.join(HERE, "preds", tag + ".json")
    if os.path.exists(cache):
        return json.load(open(cache)), tag
    from ultralytics import YOLO
    model = YOLO(weights)
    names = model.names
    out, t0 = {}, time.time()
    for it in items:
        r = model.predict(os.path.join(TD, it["image"]), imgsz=imgsz, conf=0.05, iou=0.6, max_det=1000,
                          verbose=False, augment=(extra == "tta"))[0]
        b = r.boxes
        out[it["id"]] = [[names[int(c)], [float(x) for x in xyxy], float(s)]
                         for c, xyxy, s in zip(b.cls.tolist(), b.xyxy.tolist(), b.conf.tolist())]
    print(f"{tag}: {len(items)} images in {time.time() - t0:.0f}s", flush=True)
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    json.dump(out, open(cache, "w"))
    return out, tag


def main():
    weights, imgsz = sys.argv[1], int(sys.argv[2])
    extra = os.environ.get("EXTRA")
    confs = [float(c) for c in sys.argv[3:]] or [0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5]
    items = [json.loads(l) for l in open(os.path.join(TD, "benchmark", "detection_gt.jsonl"))]
    split = os.environ.get("SPLIT", "bench")
    if split == "calib":  # VisDrone val images that are not in the benchmark
        used = {it["image"] for it in items}
        d = "image/visdrone_det_val"
        items = [{"id": f[:-4], "image": f"{d}/images/{f}", "annotation": f"{d}/annotations/{f[:-4]}.txt"}
                 for f in sorted(os.listdir(os.path.join(TD, d, "images"))) if f"{d}/images/{f}" not in used]
    preds, tag = predict(weights, imgsz, items, extra, split)
    gts = {it["id"]: gt_objects(os.path.join(TD, it["annotation"])) for it in items}
    for conf in confs:
        row = []
        for name, compat in (("exact", lambda a, b: a == b), ("group", lambda a, b: GROUP.get(a) == GROUP.get(b))):
            T = F = N = I = 0
            for it in items:
                p = [(l, tuple(b)) for l, b, s in preds[it["id"]] if s >= conf]
                tp, fp, fn, ign = match(p, gts[it["id"]], compat)
                T += tp; F += fp; N += fn; I += ign
            row.append(f"{name} P {T / max(T + F, 1):.3f} miss {N / max(T + N, 1):.3f}")
        print(f"{tag} conf {conf:.2f}  " + "  |  ".join(row), flush=True)


main()
