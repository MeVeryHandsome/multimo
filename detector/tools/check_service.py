"""Score a running detector service through its HTTP API with the backend
testsuite's protocol, on the benchmark (100 VisDrone val images) or, with
SPLIT=calib, on the calibration split.

    TESTDATA_DIR=../../testdata python check_service.py URL CONF
    # e.g. check_service.py http://127.0.0.1:18010 0.46

Set DETECTOR_API_KEY when the service requires one."""
import base64
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import select_op as so  # noqa: E402  (shares the matching rules and splits)


def main():
    url, conf = sys.argv[1].rstrip("/"), float(sys.argv[2])
    split = os.environ.get("SPLIT", "bench")
    items = so.items_for(split)
    health = json.load(urllib.request.urlopen(url + "/health", timeout=10))
    T = F = N = 0
    lat = []
    t0 = time.time()
    for it in items:
        with open(os.path.join(so.TD, it["image"]), "rb") as f:
            body = json.dumps({"image": base64.b64encode(f.read()).decode(), "conf": conf}).encode()
        headers = {"Content-Type": "application/json"}
        if os.environ.get("DETECTOR_API_KEY"):
            headers["Authorization"] = "Bearer " + os.environ["DETECTOR_API_KEY"]
        req = urllib.request.Request(url + "/v1/detect", data=body, headers=headers)
        r = json.load(urllib.request.urlopen(req, timeout=120))
        lat.append(r["latency_ms"])
        preds = [(o["label"], tuple(o["bbox"])) for o in r["objects"]]
        gt = so.ev["gt_objects"](os.path.join(so.TD, it["annotation"]))
        tp, fp, fn, _ = so.ev["match"](preds, gt, lambda a, b: a == b)
        T += tp
        F += fp
        N += fn
    lat.sort()
    print(f"{health['model']} scales {health.get('scales')} conf {conf:.2f} {split}: {len(items)} images, "
          f"TP {T} FP {F} FN {N}, precision {T / max(T + F, 1):.3f}, miss {N / max(T + N, 1):.3f}, "
          f"median latency {lat[len(lat) // 2]:.0f} ms, wall {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
