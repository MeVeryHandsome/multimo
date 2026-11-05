"""Per-class confidence thresholds chosen on the calibration split.

With class-exact matching every prediction can only match ground truth of its
own class, so TP and FP decompose by class (FN_c = visible class-c objects -
TP_c). We maximise total TP subject to overall precision >= TARGET on calib
(Lagrangian sweep: for a multiplier mu each class independently maximises
TP_c + mu * ((1-T) TP_c - T FP_c)), then score the benchmark once with those
thresholds. Tried for the released model: it gained 0.2 points on calib and
lost 0.5 on the benchmark, so the service keeps one global threshold.

    TESTDATA_DIR=../../testdata TARGET=0.86 python classconf.py TAG [TAG ...]"""
import os
import sys
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import select_op as so  # noqa: E402

GRID = np.round(np.arange(0.20, 0.81, 0.01), 2)


def image_counts(args):
    preds, g, classes = args
    out = {}
    for c in classes:
        vis = sum(1 for l, _, v, r in g if l == c and v and not r)
        pc = [(l, b, s) for l, b, s in preds if l == c]
        a = np.zeros((len(GRID), 3), dtype=np.int64)
        for i, t in enumerate(GRID):
            p = [(l, tuple(b)) for l, b, s in pc if s >= t]
            tp, fp, _, _ = so.ev["match"](p, g, lambda x, y: x == y)
            a[i] = (tp, fp, vis - tp)
        out[c] = a
    return out


def per_class_counts(preds, items, gts):
    classes = sorted({l for it in items for l, *_ in gts[it["id"]] if l != "ignored"})
    with Pool(min(64, os.cpu_count() or 1)) as pool:
        parts = pool.map(image_counts, [(preds[it["id"]], gts[it["id"]], classes) for it in items], chunksize=4)
    return {c: sum(p[c] for p in parts) for c in classes}


def choose(counts, target):
    best = None
    for mu in np.concatenate([np.linspace(0, 5, 201), np.linspace(5, 100, 191)]):
        th, T, F, N = {}, 0, 0, 0
        for c, a in counts.items():
            i = int(np.argmax(a[:, 0] + mu * ((1 - target) * a[:, 0] - target * a[:, 1])))
            th[c] = float(GRID[i])
            T += a[i, 0]
            F += a[i, 1]
            N += a[i, 2]
        if T / max(T + F, 1) >= target and (best is None or T > best[1]):
            best = (th, T, F, N)
    return best


def total(counts, th):
    T = F = N = 0
    for c, a in counts.items():
        i = int(np.argmin(np.abs(GRID - th[c])))
        T += a[i, 0]
        F += a[i, 1]
        N += a[i, 2]
    return T, F, N


def main():
    tags = sys.argv[1:]
    target = float(os.environ.get("TARGET", "0.86"))
    res = {}
    for split in ("calib", "bench"):
        items = so.items_for(split)
        gts = {it["id"]: so.ev["gt_objects"](os.path.join(so.TD, it["annotation"])) for it in items}
        res[split] = per_class_counts(so.load(tags, split), items, gts)
    th, T, F, N = choose(res["calib"], target)
    bT, bF, bN = total(res["bench"], th)
    print(" + ".join(tags), "per-class:", ", ".join(f"{c} {t:.2f}" for c, t in sorted(th.items())))
    print(f"  calib P {T / (T + F):.3f} miss {N / (T + N):.3f}  |  bench P {bT / (bT + bF):.3f} miss {bN / (bT + bN):.3f}"
          f"  (TP {bT} FP {bF} FN {bN})")


if __name__ == "__main__":
    main()
