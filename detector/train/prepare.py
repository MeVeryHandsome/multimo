"""Prepare VisDrone2019-DET for training the Multimo detector (YOLO format).

    python prepare.py DATA_DIR TESTDATA_DIR OUT_DIR

DATA_DIR holds VisDrone2019-DET-train, VisDrone2019-DET-val and
VisDrone2019-DET-test-dev, each with images/ and annotations/. Training uses
train + test-dev. The val images that are NOT in the Multimo benchmark
(TESTDATA_DIR/benchmark/detection_gt.jsonl) become the validation split, which
is also where the confidence threshold is calibrated, so the 100 benchmark
images are never seen during training or tuning. Images are symlinked.
Writes OUT_DIR/visdrone.yaml for train.py."""
import json
import os
import sys

from PIL import Image

NAMES = ["pedestrian", "people", "bicycle", "car", "van", "truck", "tricycle", "awning-tricycle", "bus", "motor"]


def convert(src_dir, out_dir, skip=frozenset()):
    img_dir, ann_dir = os.path.join(src_dir, "images"), os.path.join(src_dir, "annotations")
    oi, ol = os.path.join(out_dir, "images"), os.path.join(out_dir, "labels")
    os.makedirs(oi, exist_ok=True)
    os.makedirs(ol, exist_ok=True)
    n_img = n_obj = 0
    for f in sorted(os.listdir(img_dir)):
        if not f.lower().endswith(".jpg") or f[:-4] in skip:
            continue
        src, dst = os.path.abspath(os.path.join(img_dir, f)), os.path.join(oi, f)
        if not os.path.exists(dst):
            os.symlink(src, dst)
        w, h = Image.open(src).size
        lines = []
        for row in open(os.path.join(ann_dir, f[:-4] + ".txt")):
            v = row.strip().rstrip(",").split(",")
            if len(v) < 6:
                continue
            x, y, bw, bh, score, cat = (int(t) for t in v[:6])
            if score == 0 or cat < 1 or cat > 10 or bw <= 0 or bh <= 0:
                continue  # ignored regions (0) and "others" (11)
            cx, cy = (x + bw / 2) / w, (y + bh / 2) / h
            lines.append(f"{cat - 1} {min(cx, 1):.6f} {min(cy, 1):.6f} {min(bw / w, 1):.6f} {min(bh / h, 1):.6f}")
        with open(os.path.join(ol, f[:-4] + ".txt"), "w") as fh:
            fh.write("\n".join(lines) + ("\n" if lines else ""))
        n_img += 1
        n_obj += len(lines)
    print(f"{out_dir}: {n_img} images, {n_obj} objects")


def main():
    data, testdata, out = sys.argv[1:4]
    bench = set()
    for line in open(os.path.join(testdata, "benchmark", "detection_gt.jsonl")):
        it = json.loads(line)
        if "visdrone_det_val" in it["image"]:
            bench.add(os.path.splitext(os.path.basename(it["image"]))[0])
    print(f"{len(bench)} benchmark images held out")
    convert(os.path.join(data, "VisDrone2019-DET-train"), os.path.join(out, "train"))
    convert(os.path.join(data, "VisDrone2019-DET-test-dev"), os.path.join(out, "testdev"))
    convert(os.path.join(data, "VisDrone2019-DET-val"), os.path.join(out, "calib"), skip=bench)
    assert not any(os.path.exists(os.path.join(out, "calib", "images", b + ".jpg")) for b in bench)
    with open(os.path.join(out, "visdrone.yaml"), "w") as fh:
        fh.write(f"path: {os.path.abspath(out)}\ntrain:\n  - train/images\n  - testdev/images\nval: calib/images\nnames:\n")
        for i, n in enumerate(NAMES):
            fh.write(f"  {i}: {n}\n")


if __name__ == "__main__":
    main()
