"""Weighted box fusion of several detection passes (scales, flips or models).

Shared by the service (app.py) and the evaluation tools, so offline numbers
and the deployed service fuse boxes the same way.

Each pass is a list of (label, [x1, y1, x2, y2], score). Within one label,
boxes are visited from the highest score down; a box joins the first existing
cluster whose fused box it overlaps with IoU >= iou_thr, otherwise it starts a
new cluster. A cluster's box is the score-weighted mean of its members, and
its score is the mean over passes of each pass's best member score (a pass
that has no member contributes 0), so a box seen by only one of k passes
scores at most 1/k."""
import numpy as np


def wbf(passes, iou_thr=0.55):
    k = len(passes)
    if k == 1:
        return [(l, list(b), s) for l, b, s in passes[0]]
    dets = sorted(((l, np.asarray(b, dtype=float), s, m) for m, ds in enumerate(passes) for l, b, s in ds),
                  key=lambda d: -d[2])
    out = []
    for label in {d[0] for d in dets}:
        sums, weights, scores, boxes = [], [], [], np.zeros((0, 4))
        for _, b, s, m in (d for d in dets if d[0] == label):
            if len(boxes):
                ix = np.clip(np.minimum(b[2], boxes[:, 2]) - np.maximum(b[0], boxes[:, 0]), 0, None)
                iy = np.clip(np.minimum(b[3], boxes[:, 3]) - np.maximum(b[1], boxes[:, 1]), 0, None)
                inter = ix * iy
                union = (b[2] - b[0]) * (b[3] - b[1]) + (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1]) - inter
                ious = inter / np.maximum(union, 1e-9)
                j = int(ious.argmax())
                if ious[j] >= iou_thr:
                    sums[j] = sums[j] + s * b
                    weights[j] += s
                    scores[j][m] = max(scores[j][m], s)
                    boxes[j] = sums[j] / weights[j]
                    continue
            sums.append(s * b)
            weights.append(s)
            scores.append([0.0] * k)
            scores[-1][m] = s
            boxes = np.vstack([boxes, b])
        out += [(label, box.tolist(), sum(sc) / k) for box, sc in zip(boxes, scores)]
    return out
