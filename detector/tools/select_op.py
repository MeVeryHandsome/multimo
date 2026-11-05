"""Choose the operating point on the calibration split and report the
benchmark at that point, for one model or a weighted-box-fusion ensemble of
several, from the predictions eval_visdrone.py cached in ./preds.

    TESTDATA_DIR=../../testdata python select_op.py TAG [TAG ...]
    # e.g. select_op.py v2-1280-epoch13_1280 v2-1280-epoch13_1536

For each TAG it reads preds/TAG_calib.json and preds/TAG.json. The threshold
is the lowest confidence (0.01 grid) at which the calibration split reaches
TARGET class-exact precision (default 0.85; TARGET=0.86 leaves a margin for
the sampling error of a 100-image benchmark); the benchmark is then scored
once at that threshold. Several TAGs are fused per image the same way the
detector service fuses DETECTOR_SCALES (class-wise, IoU >= 0.55, mean score
over models)."""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TD = os.environ.setdefault("TESTDATA_DIR", os.path.join(HERE, "../../testdata"))
TARGET = float(os.environ.get("TARGET", "0.85"))

# Reuse the matching rules of eval_visdrone.py without running its main().
_src = open(os.path.join(HERE, "eval_visdrone.py")).read().replace("\nmain()\n", "\n")
ev = {"__file__": os.path.join(HERE, "eval_visdrone.py"), "__name__": "eval_visdrone"}
exec(compile(_src, "eval_visdrone.py", "exec"), ev)


def items_for(split):
    items = [json.loads(l) for l in open(os.path.join(TD, "benchmark", "detection_gt.jsonl"))]
    if split == "calib":
        used = {it["image"] for it in items}
        d = "image/visdrone_det_val"
        items = [{"id": f[:-4], "image": f"{d}/images/{f}", "annotation": f"{d}/annotations/{f[:-4]}.txt"}
                 for f in sorted(os.listdir(os.path.join(TD, d, "images"))) if f"{d}/images/{f}" not in used]
    return items


def iou_np(b, bs):
    ix = np.clip(np.minimum(b[2], bs[:, 2]) - np.maximum(b[0], bs[:, 0]), 0, None)
    iy = np.clip(np.minimum(b[3], bs[:, 3]) - np.maximum(b[1], bs[:, 1]), 0, None)
    inter = ix * iy
    return inter / ((b[2] - b[0]) * (b[3] - b[1]) + (bs[:, 2] - bs[:, 0]) * (bs[:, 3] - bs[:, 1]) - inter + 1e-9)


def wbf(model_preds, thr=0.55):
    """Weighted box fusion per class; score = mean over models (missing = 0)."""
    k = len(model_preds)
    if k == 1:
        return model_preds[0]
    allp = sorted([(l, b, s, m) for m, preds in enumerate(model_preds) for l, b, s in preds], key=lambda p: -p[2])
    out = []
    for label in {p[0] for p in allp}:
        clusters = []  # [sum of score * box, sum of score, best score per model]
        boxes = np.zeros((0, 4))
        for _, b, s, m in (p for p in allp if p[0] == label):
            b = np.array(b)
            if len(clusters):
                ious = iou_np(b, boxes)
                j = int(ious.argmax())
                if ious[j] >= thr:
                    c = clusters[j]
                    c[0] += s * b
                    c[1] += s
                    c[2][m] = max(c[2][m], s)
                    boxes[j] = c[0] / c[1]
                    continue
            sc = [0.0] * k
            sc[m] = s
            clusters.append([s * b, s, sc])
            boxes = np.vstack([boxes, b])
        out += [[label, bx.tolist(), sum(c[2]) / k] for c, bx in zip(clusters, boxes)]
    return out


def load(tags, split):
    suffix = "_calib" if split == "calib" else ""
    preds = [json.load(open(os.path.join(HERE, "preds", f"{t}{suffix}.json"))) for t in tags]
    return {i: wbf([p[i] for p in preds]) for i in preds[0]}


def score(preds, items, gts, conf):
    T = F = N = 0
    for it in items:
        p = [(l, tuple(b)) for l, b, s in preds[it["id"]] if s >= conf]
        tp, fp, fn, _ = ev["match"](p, gts[it["id"]], lambda a, b: a == b)
        T += tp
        F += fp
        N += fn
    return T / max(T + F, 1), N / max(T + N, 1)


def main():
    tags = sys.argv[1:]
    data = {}
    for split in ("calib", "bench"):
        items = items_for(split)
        gts = {it["id"]: ev["gt_objects"](os.path.join(TD, it["annotation"])) for it in items}
        data[split] = (load(tags, split), items, gts)
    for c in np.arange(0.35, 0.90, 0.01):
        p, m = score(*data["calib"], c)
        if p >= TARGET:
            bp, bm = score(*data["bench"], c)
            print(f"{' + '.join(tags)}: conf {c:.2f}  calib P {p:.3f} miss {m:.3f}  |  bench P {bp:.3f} miss {bm:.3f}")
            return
    print(" + ".join(tags), "never reaches the target precision")


if __name__ == "__main__":
    main()
