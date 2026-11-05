"""Train and evaluate the second-stage rescorer (see ../rescore.py).

    TESTDATA_DIR=../../testdata TARGET=0.86 SAVE=rescorer.pkl python train_rescorer.py TAG [TAG ...]

TAGs name cached passes in ./preds (see eval_visdrone.py), in the service's
pass order: every model at every scale, then the mirrored passes. Protocol,
so the benchmark stays untouched:
  * TP / FP labels for every fused box on the calibration split (448 VisDrone
    val images not in the benchmark); boxes on hidden objects or ignored
    regions are left out of training, as they are not scored;
  * 5-fold cross-validation by image gives out-of-fold scores for calib, and
    the threshold is the lowest one where those reach TARGET precision;
  * the final model is fit on all of calib and scored once on the benchmark."""
import json
import os
import pickle
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]
import rescore  # noqa: E402  (the feature definition the service uses)
import select_op as so  # noqa: E402

ev = so.ev
PARAMS = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=40, l2_regularization=1.0)


def label_boxes(keep, gt):
    """1 = would be a TP, 0 = FP, -1 = not scored (hidden object / ignored region)."""
    order = np.argsort([-d[2].mean() for d in keep])
    used = set()
    y = np.zeros(len(keep), int)
    for i in order:
        l, b = keep[i][0], tuple(keep[i][1])
        best, bj = 0.5, None
        for j, (gl, gb, vis, reg) in enumerate(gt):
            if vis and not reg and gl == l and j not in used:
                v = ev["iou"](b, gb)
                if v >= best:
                    best, bj = v, j
        if bj is not None:
            used.add(bj)
            y[i] = 1
        elif any((reg and ev["inside"](b, gb) >= 0.5) or (not vis and not reg and ev["iou"](b, gb) >= 0.5)
                 for _, gb, vis, reg in gt):
            y[i] = -1
    return y


def build(tags, split):
    items = so.items_for(split)
    suffix = "_calib" if split == "calib" else ""
    raw = [json.load(open(os.path.join(HERE, "preds", f"{t}{suffix}.json"))) for t in tags]
    vocab = set(rescore.CLASSES)
    data = []
    for it in items:
        W, H = Image.open(os.path.join(so.TD, it["image"])).size
        passes = [[d for d in r[it["id"]] if d[0] in vocab] for r in raw]
        keep, X = rescore.features(rescore.fuse(passes), W, H)
        gt = ev["gt_objects"](os.path.join(so.TD, it["annotation"]))
        data.append((it, keep, X, label_boxes(keep, gt) if keep else np.zeros(0, int), gt))
    return items, data


def as_preds(data, scores):
    return {it["id"]: [(k[0], tuple(k[1]), float(s)) for k, s in zip(keep, sc)]
            for (it, keep, _, _, _), sc in zip(data, scores)}


def sweep(preds, items, gts, target):
    for c in np.round(np.arange(0.20, 0.95, 0.01), 2):
        p, m = so.score(preds, items, gts, c)
        if p >= target:
            return float(c), p, m
    raise SystemExit("never reaches the target precision")


def main():
    import sklearn
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.model_selection import GroupKFold

    tags = sys.argv[1:]
    target = float(os.environ.get("TARGET", "0.86"))
    items, data = build(tags, "calib")
    gts = {d[0]["id"]: d[4] for d in data}
    rows = [d for d in data if len(d[1])]
    X = np.vstack([d[2] for d in rows])
    y = np.concatenate([d[3] for d in rows])
    g = np.concatenate([np.full(len(d[1]), n) for n, d in enumerate(rows)])
    print(f"calib: {len(y)} fused boxes, TP {(y == 1).sum()} FP {(y == 0).sum()} not scored {(y == -1).sum()}")
    mean_col = len(tags)  # features start with the per-pass scores, then their mean
    base = sweep(as_preds(data, [d[2][:, mean_col] if len(d[1]) else [] for d in data]), items, gts, target)
    print(f"mean score:   conf {base[0]:.2f}  calib P {base[1]:.3f} miss {base[2]:.3f}")

    oof = np.zeros(len(y))
    scored = y >= 0
    for fold, (a, b) in enumerate(GroupKFold(5).split(X, y, g)):
        a = a[scored[a]]
        oof[b] = HistGradientBoostingClassifier(random_state=fold, **PARAMS).fit(X[a], y[a]).predict_proba(X[b])[:, 1]
    per_image, pos = [], 0
    for d in data:
        per_image.append(oof[pos:pos + len(d[1])])
        pos += len(d[1])
    cal = sweep(as_preds(data, per_image), items, gts, target)
    print(f"rescored:     conf {cal[0]:.2f}  calib P {cal[1]:.3f} miss {cal[2]:.3f}  (out of fold)")

    model = HistGradientBoostingClassifier(random_state=0, **PARAMS).fit(X[scored], y[scored])
    bitems, bdata = build(tags, "bench")
    bgts = {d[0]["id"]: d[4] for d in bdata}
    bp, bm = so.score(as_preds(bdata, [model.predict_proba(d[2])[:, 1] if len(d[1]) else [] for d in bdata]),
                      bitems, bgts, cal[0])
    b0 = so.score(as_preds(bdata, [d[2][:, mean_col] if len(d[1]) else [] for d in bdata]), bitems, bgts, base[0])
    print(f"benchmark:    mean score P {b0[0]:.3f} miss {b0[1]:.3f}  |  rescored P {bp:.3f} miss {bm:.3f}")
    if os.environ.get("SAVE"):
        with open(os.environ["SAVE"], "wb") as f:
            pickle.dump({"model": model, "passes": len(tags), "tags": tags, "min_score": rescore.MIN_SCORE,
                         "threshold": cal[0], "target": target, "classes": rescore.CLASSES,
                         "sklearn": sklearn.__version__,
                         "calib": {"precision": cal[1], "miss": cal[2]},
                         "benchmark": {"precision": bp, "miss": bm}}, f)
        print("saved", os.environ["SAVE"])


if __name__ == "__main__":
    main()
