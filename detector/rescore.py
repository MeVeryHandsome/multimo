"""Second-stage rescoring of fused detections.

At the operating point most missed objects do have a correct fused box, but
its mean score ranks below false positives. A gradient-boosted classifier
re-scores every fused box from what the mean throws away: each pass's score,
how many passes saw it, class, size, position, crowding, duplicates and
competing boxes of other classes (e.g. people vs pedestrian on the same spot).

This module holds the feature definition, shared by the service and the
training tool (tools/train_rescorer.py), so the deployed features always match the
ones the model was trained on. A rescorer file is a pickle with keys:
model (sklearn classifier), passes (int), min_score, threshold, classes,
groups, sklearn (version it was trained with) and a description."""
import numpy as np

from fusion import wbf

CLASSES = ["pedestrian", "people", "bicycle", "car", "van", "truck", "tricycle", "awning-tricycle", "bus", "motor"]
GROUP = {"car": "v", "van": "v", "truck": "v", "bus": "v", "pedestrian": "p", "people": "p",
         "bicycle": "b", "motor": "b", "tricycle": "t", "awning-tricycle": "t"}
MIN_SCORE = 0.10  # fused boxes below this mean score are dropped before rescoring


def iou_matrix(a, b):
    if not len(a) or not len(b):
        return np.zeros((len(a), len(b)))
    a, b = np.asarray(a, float), np.asarray(b, float)
    ix = np.clip(np.minimum(a[:, None, 2], b[None, :, 2]) - np.maximum(a[:, None, 0], b[None, :, 0]), 0, None)
    iy = np.clip(np.minimum(a[:, None, 3], b[None, :, 3]) - np.maximum(a[:, None, 1], b[None, :, 1]), 0, None)
    inter = ix * iy
    aa = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    bb = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / np.maximum(aa[:, None] + bb[None, :] - inter, 1e-9)


def fuse(passes):
    """Fused boxes as (label, box array, per-pass scores)."""
    return [(l, np.asarray(b, float), sc) for l, b, _, sc in wbf(passes, detail=True)]


def features(fused, width, height, min_score=MIN_SCORE):
    """Kept fused boxes (mean score >= min_score) and their feature matrix."""
    keep = [d for d in fused if d[2].mean() >= min_score]
    if not keep:
        return keep, np.zeros((0, 0))
    W, H = float(width), float(height)
    labels = [d[0] for d in keep]
    boxes = np.array([d[1] for d in keep])
    S = np.array([d[2] for d in keep])
    mean = S.mean(1)
    w = np.maximum(boxes[:, 2] - boxes[:, 0], 1)
    h = np.maximum(boxes[:, 3] - boxes[:, 1], 1)
    cx, cy = (boxes[:, 0] + boxes[:, 2]) / 2, (boxes[:, 1] + boxes[:, 3]) / 2
    iou = iou_matrix(boxes, boxes)
    np.fill_diagonal(iou, 0)
    lab = np.array(labels)
    same = lab[:, None] == lab[None, :]
    grp = np.array([GROUP[l] for l in labels])
    same_grp = (grp[:, None] == grp[None, :]) & ~same
    strong = mean >= 0.3
    feats = []
    for i in range(len(keep)):
        comp = iou[i] * ~same[i]
        j = int(comp.argmax())
        comp_iou = comp[j]
        comp_score = mean[j] if comp_iou > 0 else 0.0
        comp_same_group = float(same_grp[i, j]) if comp_iou > 0 else 0.0
        gcomp = iou[i] * same_grp[i]
        gj = int(gcomp.argmax())
        dup = iou[i] * same[i]
        dist = np.hypot(cx - cx[i], cy - cy[i])
        near = (dist < 3 * max(w[i], h[i])) & strong
        near[i] = False
        same_cls_scores = mean[same[i] | (np.arange(len(keep)) == i)]
        f = list(S[i]) + [mean[i], S[i].max(), S[i].min(), S[i].std(), (S[i] > 0).sum(),
             np.log(w[i]), np.log(h[i]), np.log(w[i] * h[i]), np.log(w[i] / h[i]),
             cy[i] / H, cx[i] / W, np.log(w[i] * h[i] / (W * H)),
             np.log1p(strong.sum()), np.log1p(near.sum()), np.log1p((near & same[i]).sum()),
             comp_iou, comp_score, comp_same_group, comp_score - mean[i],
             gcomp[gj], mean[gj] if gcomp[gj] > 0 else 0.0,
             dup.max(), (same_cls_scores > mean[i]).mean(),
             np.mean(mean[strong]) if strong.any() else 0.0]
        f += [float(labels[i] == c) for c in CLASSES]
        feats.append(f)
    return keep, np.array(feats, float)


def apply(rescorer, passes, width, height):
    """Rescore the passes of one image: [(label, box list, probability)]."""
    if len(passes) != rescorer["passes"]:
        raise ValueError(f"rescorer expects {rescorer['passes']} passes, got {len(passes)}")
    keep, X = features(fuse(passes), width, height, rescorer.get("min_score", MIN_SCORE))
    if not keep:
        return []
    p = rescorer["model"].predict_proba(X)[:, 1]
    return [(k[0], k[1].tolist(), float(s)) for k, s in zip(keep, p)]
