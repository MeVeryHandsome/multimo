#!/usr/bin/env python3
"""Build the evaluation sets in testdata/benchmark/ from dataset annotations.

Every reference answer and relevance judgement is computed from the datasets'
own ground truth (VisDrone / UAVDT / DOTA annotations, UAV-VisLoc and DJI GPS
logs) or quoted from the source documents. Nothing here comes from Multimo's
own model outputs.

    python3 -I scripts/lib/build_benchmark.py            # writes testdata/benchmark/*

Outputs
    corpus.json          assets the evaluation ingests, grouped by type
    keyframe_gt.jsonl    videos with per-frame annotations (keyframe consistency)
    detection_gt.jsonl   images with box annotations (detection precision / miss rate)
    geo_gt.jsonl         images and video with GPS ground truth (localisation error)
    queries.jsonl        50 retrieval queries with relevant asset sets
    qa_pairs.jsonl       50 question / reference answer pairs (+ curated doc facts)
"""
from __future__ import annotations

import collections
import csv
import json
import math
import re
import struct
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TD = ROOT / "testdata"
OUT = TD / "benchmark"
FPS = 25  # must match scripts/prepare_testdata.sh

VISDRONE = {1: "pedestrian", 2: "people", 3: "bicycle", 4: "car", 5: "van", 6: "truck",
            7: "tricycle", 8: "awning-tricycle", 9: "bus", 10: "motor"}
VISDRONE_ZH = {"pedestrian": "行人", "people": "人群（非行走）", "bicycle": "自行车", "car": "小汽车",
               "van": "面包车", "truck": "卡车", "tricycle": "三轮车", "awning-tricycle": "带篷三轮车",
               "bus": "公交车", "motor": "摩托车"}
UAVDT = {1: "car", 2: "truck", 3: "bus"}
DOTA_ZH = {"plane": "飞机", "ship": "船只", "storage-tank": "储油罐", "baseball-diamond": "棒球场",
           "tennis-court": "网球场", "basketball-court": "篮球场", "ground-track-field": "田径场",
           "harbor": "港口", "bridge": "桥梁", "large-vehicle": "大型车辆", "small-vehicle": "小型车辆",
           "helicopter": "直升机", "roundabout": "环岛", "soccer-ball-field": "足球场",
           "swimming-pool": "游泳池", "container-crane": "集装箱起重机", "airport": "机场", "helipad": "直升机停机坪"}
UAVDT_ATTR = ["daylight", "night", "fog", "low-alt", "medium-alt", "high-alt",
              "front-view", "side-view", "bird-view", "long-term"]

# An object counts as "visible" for relevance and QA when it is big enough to
# be seen in a keyframe and not heavily occluded.
MIN_AREA = 600
VIDEO_MIN_FRAME_SHARE = 0.2  # a video "contains" X if X is visible in >=20% of frames


def rel(p: Path) -> str:
    return p.relative_to(TD).as_posix()


def image_size(p: Path) -> tuple[int, int]:
    """Width/height of a JPEG or PNG without third-party libraries."""
    with open(p, "rb") as f:
        head = f.read(26)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", head[16:24])
        f.seek(2)
        while True:
            marker, size = struct.unpack(">2sH", f.read(4))
            if marker[1] in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">xHH", f.read(5))
                return w, h
            f.seek(size - 2, 1)


# --------------------------------------------------------------------------- parsers

def visdrone_det(path: Path) -> list[dict]:
    objs = []
    for line in path.read_text().splitlines():
        v = [int(x) for x in line.strip().rstrip(",").split(",")[:8]]
        x, y, w, h, score, cat, trunc, occ = v
        if cat in VISDRONE:
            objs.append({"cat": VISDRONE[cat], "box": [x, y, w, h], "trunc": trunc, "occ": occ,
                         "visible": w * h >= MIN_AREA and occ < 2})
    return objs


def visdrone_vid(path: Path) -> dict[int, list[dict]]:
    frames: dict[int, list[dict]] = collections.defaultdict(list)
    for line in path.read_text().splitlines():
        v = [int(x) for x in line.strip().rstrip(",").split(",")[:10]]
        fr, tid, x, y, w, h, score, cat, trunc, occ = v
        if cat in VISDRONE:
            frames[fr].append({"cat": VISDRONE[cat], "visible": w * h >= MIN_AREA and occ < 2})
    return frames


def uavdt_gt(path: Path) -> dict[int, list[dict]]:
    frames: dict[int, list[dict]] = collections.defaultdict(list)
    for line in path.read_text().splitlines():
        fr, tid, x, y, w, h, oov, occ, cat = [int(x) for x in line.split(",")[:9]]
        frames[fr].append({"cat": UAVDT[cat], "visible": w * h >= MIN_AREA and occ < 3})
    return frames


def dota_labels(path: Path) -> list[dict]:
    objs = []
    for line in path.read_text().splitlines():
        t = line.split()
        if len(t) < 10:
            continue  # header lines such as "imagesource:" / "gsd:"
        objs.append({"cat": t[8], "difficult": int(t[9])})
    return objs


def frame_share(frames: dict[int, list[dict]], n_frames: int) -> dict[str, float]:
    hits = collections.Counter()
    for objs in frames.values():
        for c in {o["cat"] for o in objs if o["visible"]}:
            hits[c] += 1
    return {c: round(n / n_frames, 3) for c, n in hits.items()}


def mean_visible(frames: dict[int, list[dict]], n_frames: int) -> dict[str, float]:
    tot = collections.Counter()
    for objs in frames.values():
        tot.update(o["cat"] for o in objs if o["visible"])
    return {c: round(n / n_frames, 1) for c, n in tot.items()}


# --------------------------------------------------------------------------- builders

def build_videos() -> list[dict]:
    vids = []
    for ann in sorted((TD / "video/visdrone_vid_val/annotations").glob("*.txt")):
        seq = ann.stem
        frames_dir = TD / "video/visdrone_vid_val/sequences" / seq
        if not frames_dir.is_dir():
            continue
        jpgs = sorted(frames_dir.glob("*.jpg"))
        w, h = image_size(jpgs[0])
        frames = visdrone_vid(ann)
        vids.append({"id": f"visdrone_vid:{seq}", "video": f"video/visdrone_vid_val/mp4/{seq}.mp4",
                     "frames_dir": rel(frames_dir), "annotation": rel(ann), "format": "visdrone-vid",
                     "fps": FPS, "frames": len(jpgs), "width": w, "height": h,
                     "frame_share": frame_share(frames, len(jpgs)),
                     "mean_visible_per_frame": mean_visible(frames, len(jpgs))})
    attrs = {}
    for p in (TD / "video/uavdt/M_attr").glob("*/*.txt"):
        flags = [int(x) for x in p.read_text().strip().split(",")]
        attrs[p.name.replace(" ", "").split("_")[0]] = [a for a, f in zip(UAVDT_ATTR, flags) if f]
    for seq_dir in sorted((TD / "video/uavdt/UAV-benchmark-M").glob("M*")):
        seq = seq_dir.name
        gt = TD / f"video/uavdt/GT/{seq}_gt_whole.txt"
        jpgs = sorted(seq_dir.glob("*.jpg"))
        w, h = image_size(jpgs[0])
        frames = uavdt_gt(gt)
        vids.append({"id": f"uavdt:{seq}", "video": f"video/uavdt/mp4/{seq}.mp4", "frames_dir": rel(seq_dir),
                     "annotation": rel(gt), "format": "uavdt", "fps": FPS, "frames": len(jpgs),
                     "width": w, "height": h, "attributes": attrs.get(seq, []),
                     "frame_share": frame_share(frames, len(jpgs)),
                     "mean_visible_per_frame": mean_visible(frames, len(jpgs))})
    return vids


def build_det_images(n: int = 100) -> list[dict]:
    imgs = sorted((TD / "image/visdrone_det_val/images").glob("*.jpg"))[::5][:n]
    out = []
    for img in imgs:
        ann = TD / "image/visdrone_det_val/annotations" / (img.stem + ".txt")
        objs = visdrone_det(ann)
        w, h = image_size(img)
        out.append({"id": f"visdrone_det:{img.stem}", "image": rel(img), "annotation": rel(ann),
                    "format": "visdrone-det", "width": w, "height": h,
                    "visible_counts": dict(collections.Counter(o["cat"] for o in objs if o["visible"])),
                    "all_counts": dict(collections.Counter(o["cat"] for o in objs))})
    return out


def build_dota() -> list[dict]:
    out = []
    for img in sorted((TD / "remote_sensing/dota_v2_val/images").glob("*.png")):
        lab = TD / "remote_sensing/dota_v2_val/labelTxt" / (img.stem + ".txt")
        objs = dota_labels(lab)
        w, h = image_size(img)
        out.append({"id": f"dota:{img.stem}", "image": rel(img), "annotation": rel(lab), "format": "dota-v2",
                    "width": w, "height": h,
                    "counts": dict(collections.Counter(o["cat"] for o in objs if not o["difficult"]))})
    return out


def build_geo() -> tuple[list[dict], dict]:
    rows = list(csv.DictReader(open(TD / "geo/uav_visloc/01/01.csv", encoding="utf-8")))
    by_name = {r["filename"]: r for r in rows}
    out = []
    for img in sorted((TD / "geo/uav_visloc/01/drone").glob("*.JPG")):
        r = by_name[img.name]
        out.append({"id": f"visloc:{img.stem}", "image": rel(img), "lat": float(r["lat"]), "lon": float(r["lon"]),
                    "height_m": float(r["height"]), "time": r["date"], "satellite": "geo/uav_visloc/01/satellite01.tif"})

    # DJI Palos Verdes: GPS track from the flight record, aligned by timestamp.
    csv_path = TD / "video/dji/palos_verdes/flight_record.csv"
    track = []
    with open(csv_path, encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            try:
                track.append((float(r["timestamps_ns"]), float(r["OSD.latitude"]), float(r["OSD.longitude"]),
                              float(r["OSD.height [ft]"] or 0)))
            except (ValueError, KeyError):
                continue
    track = [t for t in track if abs(t[1]) > 1]
    first = next(iter(csv.DictReader(open(csv_path, encoding="utf-8", errors="replace"))))
    dji = {"id": "dji:palos_verdes", "video": "video/dji/palos_verdes/drone_palos_verdes_20250217174636.mp4",
           "telemetry": rel(csv_path), "drone_model": first.get("RECOVER.aircraftName") or first.get("OSD.droneType"),
           "track_start_utc": datetime.fromtimestamp(track[0][0], timezone.utc).isoformat(),
           "track_end_utc": datetime.fromtimestamp(track[-1][0], timezone.utc).isoformat(),
           "track_points": len(track),
           "lat_range": [min(t[1] for t in track), max(t[1] for t in track)],
           "lon_range": [min(t[2] for t in track), max(t[2] for t in track)],
           "max_height_ft": max(t[3] for t in track),
           "home": {"lat": track[0][1], "lon": track[0][2]}}
    return out, dji


# --------------------------------------------------------------------------- documents

DOCS = {
    "docs/faa/CFR-2024-title14-part107.pdf": "pdf",
    "docs/faa/remote_pilot_study_guide.pdf": "pdf",
    "docs/faa/AC_107-2A.pdf": "pdf",
    "docs/faa/Part_107_Summary.pdf": "pdf",
    "docs/cn/uav_flight_regulation_2023.doc": "doc",
    "docs/uk/pdra01_operations_manual_template.docx": "docx",
    "docs/geo_text/ne_10m_airports.geojson": "text",
    "docs/geo_text/ne_10m_populated_places.geojson": "text",
}


def doc_text(path: str, kind: str) -> str:
    p = TD / path
    if kind == "pdf":
        t = subprocess.run(["pdftotext", str(p), "-"], capture_output=True, check=True, text=True).stdout
    elif kind == "doc":
        t = subprocess.run(["catdoc", "-d", "utf-8", "-w", str(p)], capture_output=True, check=True, text=True).stdout
    elif kind == "docx":
        xml = zipfile.ZipFile(p).read("word/document.xml").decode("utf-8")
        t = re.sub(r"<[^>]+>", "", re.sub(r"</w:p>", "\n", xml))
    else:
        t = p.read_text(encoding="utf-8")
    return t


def norm(s: str) -> str:
    """Comparable form: no line-break hyphenation, no whitespace, straight quotes, lower case."""
    s = re.sub(r"-\s*\n\s*", "", s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", "", s).lower()


# --------------------------------------------------------------------------- geometry

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# --------------------------------------------------------------------------- corpus

def build_corpus(vids, dets, dota, geo, dji) -> dict:
    return {
        "note": "评测时接入的全部素材。collection 用于检索时限定范围；visloc 只带拍摄时间和参考底图所在集合"
                "（任务区域已知），不带坐标：坐标是定位评测的真值，必须由系统自己推算。",
        "assets": (
            [{"path": v["video"], "type": "video", "collection": v["id"].split(":")[0]} for v in vids]
            + [{"path": dji["video"], "type": "video", "collection": "dji", "sidecar": dji["telemetry"]}]
            + [{"path": f"video/formats/{n}", "type": "video", "collection": "formats"}
               for n in ("uav0000339_00001_v.wmv", "uav0000339_00001_v.flv", "M1004.wmv", "M1004.flv")]
            + [{"path": d["image"], "type": "image", "collection": "visdrone_det"} for d in dets]
            + [{"path": d["image"], "type": "remote_sensing", "collection": "dota"} for d in dota]
            + [{"path": "remote_sensing/geotiff/cea.tif", "type": "remote_sensing", "collection": "geotiff"},
               {"path": "geo/uav_visloc/01/satellite01.tif", "type": "remote_sensing", "collection": "basemap"}]
            + [{"path": g["image"], "type": "image", "collection": "visloc",
                "metadata": {"time": g["time"], "geo_reference": "basemap"}} for g in geo]
            + [{"path": p, "type": "document", "collection": "docs"} for p in DOCS]
        ),
    }


# --------------------------------------------------------------------------- retrieval queries

def build_queries(vids, dets, dota, geo, dji, texts) -> list[dict]:
    qs: list[dict] = []

    def add(qtype, text, collections, relevant, rule):
        qs.append({"id": f"q{len(qs) + 1:02d}", "type": qtype, "query": text, "collections": collections,
                   "relevant": sorted(relevant), "rule": rule})

    vc = lambda d, c: d["visible_counts"].get(c, 0)
    det_specs = [
        ("街道上有公交车的航拍图像", lambda d: vc(d, "bus") >= 1, "可见公交车 ≥ 1"),
        ("画面里有卡车的航拍图像", lambda d: vc(d, "truck") >= 1, "可见卡车 ≥ 1"),
        ("出现带篷三轮车的路口或街道", lambda d: vc(d, "awning-tricycle") >= 1, "可见带篷三轮车 ≥ 1"),
        ("有人骑自行车的航拍画面", lambda d: vc(d, "bicycle") >= 1, "可见自行车 ≥ 1"),
        ("摩托车很多的街景（10 辆以上）", lambda d: vc(d, "motor") >= 10, "可见摩托车 ≥ 10"),
        ("行人密集的场景（20 人以上）", lambda d: vc(d, "pedestrian") >= 20, "可见行人 ≥ 20"),
        ("停满小汽车的停车场或道路（40 辆以上）", lambda d: vc(d, "car") >= 40, "可见小汽车 ≥ 40"),
        ("几乎看不到小汽车的画面", lambda d: vc(d, "car") <= 2, "可见小汽车 ≤ 2"),
        ("同时出现公交车和卡车的画面", lambda d: vc(d, "bus") >= 1 and vc(d, "truck") >= 1, "公交车 ≥ 1 且卡车 ≥ 1"),
        ("有三轮车行驶的街道", lambda d: vc(d, "tricycle") >= 1, "可见三轮车 ≥ 1"),
        ("多辆面包车（3 辆以上）", lambda d: vc(d, "van") >= 3, "可见面包车 ≥ 3"),
        ("有人群聚集（10 人以上非行走人员）", lambda d: vc(d, "people") >= 10, "可见 people 类 ≥ 10"),
    ]
    for text, cond, rule in det_specs:
        add("object", text, ["visdrone_det"], [d["image"] for d in dets if cond(d)], rule)

    share = lambda v, *cats: max(v["frame_share"].get(c, 0) for c in cats)
    vid_specs = [
        ("出现卡车的道路视频", lambda v: share(v, "truck") >= VIDEO_MIN_FRAME_SHARE, "卡车可见帧占比 ≥ 20%"),
        ("拍到公交车的视频", lambda v: share(v, "bus") >= VIDEO_MIN_FRAME_SHARE, "公交车可见帧占比 ≥ 20%"),
        ("有三轮车经过的视频", lambda v: share(v, "tricycle", "awning-tricycle") >= VIDEO_MIN_FRAME_SHARE,
         "三轮车或带篷三轮车可见帧占比 ≥ 20%"),
        ("有人骑自行车的视频", lambda v: share(v, "bicycle") >= VIDEO_MIN_FRAME_SHARE, "自行车可见帧占比 ≥ 20%"),
        ("夜间拍摄的无人机视频", lambda v: "night" in v.get("attributes", []), "UAVDT 属性 night"),
        ("雾天拍摄的无人机视频", lambda v: "fog" in v.get("attributes", []), "UAVDT 属性 fog"),
    ]
    for text, cond, rule in vid_specs:
        add("video", text, ["visdrone_vid", "uavdt"], [v["video"] for v in vids if cond(v)], rule)

    dc = lambda d, c: d["counts"].get(c, 0)
    dota_specs = [
        ("有船只的遥感影像", "ship", 1), ("停有大量车辆（20 辆以上）的遥感影像", "small-vehicle", 20),
        ("有游泳池的居民区遥感影像", "swimming-pool", 1), ("港口码头的遥感影像", "harbor", 1),
        ("有桥梁的遥感影像", "bridge", 1), ("有田径场的遥感影像", "ground-track-field", 1),
        ("有环岛的遥感影像", "roundabout", 1), ("有网球场的遥感影像", "tennis-court", 1),
        ("有储油罐的遥感影像", "storage-tank", 1), ("机场的遥感影像", "airport", 1),
    ]
    for text, cat, n in dota_specs:
        add("remote_sensing", text, ["dota"], [d["image"] for d in dota if dc(d, cat) >= n],
            f"DOTA 非困难 {cat} ≥ {n}")

    # Spatial: circles around points on the UAV-VisLoc track, plus boxes.
    for idx, radius in ((5, 300), (25, 300), (45, 500), (70, 400)):
        c = geo[idx]
        lat, lon = round(c["lat"], 4), round(c["lon"], 4)
        rel_imgs = [g["image"] for g in geo if haversine_m(lat, lon, g["lat"], g["lon"]) <= radius]
        add("spatial", f"以北纬{lat}°、东经{lon}°为中心，半径{radius}米内拍摄的航拍图像", ["visloc"], rel_imgs,
            f"haversine ≤ {radius} m")
    lats = sorted(g["lat"] for g in geo)
    lo, hi = round(lats[len(lats) // 4], 4), round(lats[len(lats) // 2], 4)
    add("spatial", f"纬度在北纬{lo}°到{hi}°之间拍摄的航拍图像", ["visloc"],
        [g["image"] for g in geo if lo <= g["lat"] <= hi], "纬度落在区间内")
    lons = sorted(g["lon"] for g in geo)
    lo2 = round(lons[len(lons) * 3 // 4], 4)
    add("spatial", f"东经{lo2}°以东拍摄的航拍图像", ["visloc"], [g["image"] for g in geo if g["lon"] >= lo2],
        "经度 ≥ 阈值")
    add("spatial", "美国加利福尼亚州帕洛斯弗迪斯（Palos Verdes）一带拍摄的视频", ["visdrone_vid", "uavdt", "dji"],
        [dji["video"]], "DJI 飞行记录轨迹位于 Palos Verdes")
    add("spatial", "北纬33.73°、西经118.33°附近 2 公里内拍摄的视频", ["visdrone_vid", "uavdt", "dji"],
        [dji["video"]], "DJI 起飞点距该点 < 2 km")

    add("temporal", "2025年2月拍摄的无人机视频", ["visdrone_vid", "uavdt", "dji"], [dji["video"]],
        "DJI 飞行记录时间 2025-02")
    t0, t1 = "2018-09-17T13:00:00", "2018-09-17T13:10:00"
    add("temporal", "2018年9月17日13:00到13:10之间拍摄的航拍图像", ["visloc"],
        [g["image"] for g in geo if t0 <= g["time"] <= t1], "拍摄时间落在区间内")

    nt = {p: norm(t) for p, t in texts.items()}
    doc_specs = [
        ("美国小型无人机最大飞行高度限制", ["400 feet above ground level"]),
        ("小型无人机的最大地速限制", ["87 knots"]),
        ("无人机夜间飞行的防撞灯要求", ["anti-collision light"]),
        ("申请远程驾驶员证书的年龄要求", ["16 years of age", "16 years old"]),
        ("无人机事故后多少天内必须向 FAA 报告", ["10 calendar days", "within 10 days"]),
        ("无人机远程识别（Remote ID）的规定", ["remote id"]),
        ("中国无人驾驶航空器管制空域如何划设", ["管制空域"]),
        ("微型无人驾驶航空器的定义和重量标准", ["微型无人驾驶航空器，是指"]),
        ("无人机未实名登记的处罚", ["未经实名登记"]),
        ("英国 PDRA01 运行手册模板", ["pdra01"]),
        ("全球机场的位置与 IATA 代码数据", ["iata_code"]),
        ("世界城市与人口聚居点的地理坐标数据", ["pop_max"]),
    ]
    for text, kws in doc_specs:
        relevant = [p for p in DOCS if any(norm(k) in nt[p] for k in kws)]
        add("document", text, ["docs"], relevant, "文档全文包含：" + " / ".join(kws))
    return qs


# --------------------------------------------------------------------------- QA

CN = "docs/cn/uav_flight_regulation_2023.doc"
CFR = "docs/faa/CFR-2024-title14-part107.pdf"
UK = "docs/uk/pdra01_operations_manual_template.docx"

# Facts taken from the documents. Every quote is checked against the extracted text.
DOC_QA = [
    ("《无人驾驶航空器飞行管理暂行条例》从什么时候开始施行？", "2024年1月1日起施行。", CN,
     "自2024年1月1日起施行"),
    ("按照《无人驾驶航空器飞行管理暂行条例》，微型无人驾驶航空器需要满足哪些性能指标？",
     "空机重量小于0.25千克，最大飞行真高不超过50米，最大平飞速度不超过40千米/小时，无线电发射设备符合微功率短距离技术要求，全程可以随时人工介入操控。",
     CN, "微型无人驾驶航空器，是指空机重量小于0.25千克，最大飞行真高不超过50米，最大平飞速度不超过40千米/小时"),
    ("条例中轻型无人驾驶航空器的重量和速度标准是什么？",
     "空机重量不超过4千克且最大起飞重量不超过7千克，最大平飞速度不超过100千米/小时。", CN,
     "轻型无人驾驶航空器，是指空机重量不超过4千克且最大起飞重量不超过7千克，最大平飞速度不超过100千米/小时"),
    ("真高多少米以上的空域应当划设为无人驾驶航空器管制空域？", "真高120米以上的空域。", CN,
     "真高120米以上空域，空中禁区、空中限制区以及周边空域"),
    ("组织无人驾驶航空器飞行活动，最迟什么时候要向空中交通管理机构提出飞行活动申请？",
     "拟飞行前1日12时前（条例第三十一条另有规定的除外）。", CN,
     "应当在拟飞行前1日12时前向空中交通管理机构提出飞行活动申请"),
    ("飞行活动获得批准后，起飞前多久要向空中交通管理机构报告预计起飞时刻和准备情况？",
     "计划起飞1小时前。", CN, "应当在计划起飞1小时前向空中交通管理机构报告预计起飞时刻和准备情况"),
    ("民用无人驾驶航空器未经实名登记就飞行，会受到什么处罚？",
     "由公安机关责令改正，可以处200元以下的罚款；情节严重的，处2000元以上2万元以下的罚款。", CN,
     "民用无人驾驶航空器未经实名登记实施飞行活动的，由公安机关责令改正，可以处200元以下的罚款；情节严重的，处2000元以上2万元以下的罚款"),
    ("什么情况下农用无人驾驶航空器作业无需取得运营合格证？",
     "使用最大起飞重量不超过150千克的农用无人驾驶航空器，在农林牧渔区域上方的适飞空域内从事农林牧渔作业飞行活动。",
     CN, "使用最大起飞重量不超过150千克的农用无人驾驶航空器在农林牧渔区域上方的适飞空域内从事农林牧渔作业飞行活动"),
    ("哪些民用无人驾驶航空器的操控人员需要取得操控员执照？", "操控小型、中型、大型民用无人驾驶航空器飞行的人员。", CN,
     "操控小型、中型、大型民用无人驾驶航空器飞行的人员应当具备下列条件"),
    ("为保障大型活动临时增加的管制空域，地方政府需要提前多久发布公告？", "在临时增加的管制空域生效24小时前。", CN,
     "在临时增加的管制空域生效24小时前，由设区的市级以上地方人民政府发布公告"),
    ("美国 Part 107 规定小型无人机的最大飞行高度是多少？",
     "距地面不超过400英尺；在建筑物400英尺半径内飞行时，可以高于地面400英尺，但不得超过建筑物顶部以上400英尺。", CFR,
     "The altitude of the small unmanned aircraft cannot be higher than 400 feet above ground level"),
    ("Part 107 对小型无人机的地速上限是多少？", "87节（100英里/小时）。", CFR,
     "The groundspeed of the small unmanned aircraft may not exceed 87 knots (100 miles per hour)"),
    ("Part 107 要求小型无人机与云保持多远的距离？", "在云下方至少500英尺，与云的水平距离至少2000英尺。", CFR,
     "(1) 500 feet below the cloud; and (2) 2,000 feet horizontally from the cloud"),
    ("Part 107 中的小型无人机（small unmanned aircraft）有什么重量限制？", "起飞重量（含机上所有物品）小于55磅。", CFR,
     "Small unmanned aircraft means an unmanned aircraft weighing less than 55 pounds on takeoff"),
    ("申请带小型无人机等级的远程驾驶员证书，最低年龄是多少？", "至少16岁。", CFR, "(a) Be at least 16 years of age"),
    ("发生需要报告的安全事件后，远程机长要在多少天内向 FAA 报告？", "不晚于运行后10个日历日。", CFR,
     "No later than 10 calendar days after an operation that meets the criteria"),
    ("远程驾驶员要保持资格，需要在多长时间内完成知识考试或复训？", "前24个日历月内。", CFR,
     "within the previous 24 calendar months"),
    ("Part 107 夜间运行时，对防撞灯有什么要求？",
     "无人机要有点亮的防撞灯，可见距离至少3法定英里，闪烁频率足以避免碰撞。", CFR,
     "lighted anti-collision lighting visible for at least 3 statute miles that has a flash rate sufficient to avoid a collision"),
    ("小型无人机遇到其他航空器时的通行权规则是什么？",
     "必须给所有航空器、空中飞行器以及发射和再入飞行器让路，不得从其上方、下方或前方穿越，除非保持足够间隔。", CFR,
     "Each small unmanned aircraft must yield the right of way to all aircraft, airborne vehicles, and launch and reentry vehicles"),
    ("Part 107 对最低飞行能见度有什么要求？", "从控制站位置观测的飞行能见度不低于3法定英里。", CFR,
     "The minimum flight visibility, as observed from the location of the control station must be no less than 3 statute miles"),
    ("英国 PDRA01 运行手册模板规定的最大飞行高度是多少？", "距地表不超过120米（400英尺）。", UK,
     "Maximum height not to exceed 120 metres or 400 feet above the surface"),
    ("按照英国 PDRA01，无人机与无关人员要保持多远距离？",
     "飞行中与任何无关人员保持至少50米；起飞和降落时与无关人员保持至少30米。", UK,
     "No flight within 30 metres distance of uninvolved people during take-off and landing No flight within 50 metres of any uninvolved person"),
]


def build_qa(vids, dets, dota, geo, dji, texts) -> list[dict]:
    qa: list[dict] = []
    nt = {p: norm(t) for p, t in texts.items()}

    def add(cat, q, ref, assets, evidence, check=None):
        item = {"id": f"qa{len(qa) + 1:02d}", "category": cat, "question": q, "reference": ref,
                "assets": assets, "evidence": evidence}
        if check:
            item["check"] = check
        qa.append(item)

    for q, ref, doc, quote in DOC_QA:
        if norm(quote) not in nt[doc]:
            raise SystemExit(f"evidence not found in {doc}: {quote}")
        add("document", q, ref, [doc], [{"source": doc, "quote": quote}])

    # Images: dominant category, and bus presence.
    def dominant(counts):
        top = sorted(counts.items(), key=lambda kv: -kv[1])
        return top if len(top) >= 2 and top[0][1] >= 10 and top[0][1] >= 2 * top[1][1] else None
    dom = [(d, dominant(d["visible_counts"])) for d in dets]
    dom = [(d, t) for d, t in dom if t][:3]
    for d, top in dom:
        name = Path(d["image"]).name
        add("image", f"航拍图像 {name} 中数量最多的目标是什么？", f"{VISDRONE_ZH[top[0][0]]}（约{top[0][1]}个）。",
            [d["image"]], [{"source": d["annotation"], "computed": f"可见目标计数 {dict(top[:3])}"}])
    with_bus = [d for d in dets if d["visible_counts"].get("bus", 0) >= 2][:2]
    no_bus = [d for d in dets if d["all_counts"].get("bus", 0) == 0 and d["visible_counts"].get("car", 0) >= 15][:1]
    for d in with_bus:
        add("image", f"航拍图像 {Path(d['image']).name} 里有公交车吗？", f"有，可见约{d['visible_counts']['bus']}辆公交车。",
            [d["image"]], [{"source": d["annotation"], "computed": f"可见公交车 {d['visible_counts']['bus']}"}])
    for d in no_bus:
        add("image", f"航拍图像 {Path(d['image']).name} 里有公交车吗？", "没有公交车，画面中主要是小汽车。",
            [d["image"]], [{"source": d["annotation"], "computed": f"公交车 0，可见小汽车 {d['visible_counts']['car']}"}])

    # Videos
    v = {x["id"]: x for x in vids}
    a = v["visdrone_vid:uav0000086_00000_v"]
    top = max(a["mean_visible_per_frame"].items(), key=lambda kv: kv[1])
    add("video", "视频 uav0000086_00000_v 中最多的目标类型是什么？", f"{VISDRONE_ZH[top[0]]}，平均每帧约{top[1]:.0f}个。",
        [a["video"]], [{"source": a["annotation"], "computed": f"每帧平均可见目标 {a['mean_visible_per_frame']}"}])
    add("video", "视频 uav0000086_00000_v 中有小汽车吗？",
        "没有。画面里主要是行人、人群、摩托车和自行车，没有小汽车。" if "car" not in a["frame_share"] else "有。",
        [a["video"]], [{"source": a["annotation"], "computed": f"各类别可见帧占比 {a['frame_share']}"}])
    b = v["visdrone_vid:uav0000305_00000_v"]
    add("video", "视频 uav0000305_00000_v 中是否出现了公交车？",
        f"出现了，公交车在{b['frame_share'].get('bus', 0):.0%}的帧中可见。", [b["video"]],
        [{"source": b["annotation"], "computed": f"bus 可见帧占比 {b['frame_share'].get('bus', 0)}"}])
    m2, m4 = v["uavdt:M0202"], v["uavdt:M1004"]
    add("video", "视频 M0202 是在什么光照或天气条件下拍摄的？", "夜间拍摄。", [m2["video"]],
        [{"source": "video/uavdt/M_attr", "computed": f"属性 {m2['attributes']}"}])
    add("video", "视频 M1004 拍摄时的天气条件是什么？", "雾天。", [m4["video"]],
        [{"source": "video/uavdt/M_attr", "computed": f"属性 {m4['attributes']}"}])
    add("video", "视频 M0202 是以什么飞行高度和视角拍摄的？", "低空、侧视。", [m2["video"]],
        [{"source": "video/uavdt/M_attr", "computed": f"属性 {m2['attributes']}"}])

    # Remote sensing
    d = {x["id"]: x for x in dota}
    def dota_qa(pid, q, ref):
        x = d[f"dota:{pid}"]
        add("remote_sensing", q, ref, [x["image"]], [{"source": x["annotation"], "computed": f"目标计数 {x['counts']}"}])
    dota_qa("P6519", "遥感影像 P6519 主要拍到的是什么场景？", "港口，停靠着大量船只（标注有18处港口设施、15艘船）。")
    dota_qa("P3575", "遥感影像 P3575 中有哪些体育场地？", "网球场和棒球场（4个网球场、4个棒球场）。")
    dota_qa("P4947", "遥感影像 P4947 中有什么交通设施？", "桥梁。")
    dota_qa("P6347", "遥感影像 P6347 是什么类型的区域，有什么典型目标？", "居民区，有大量小型车辆和游泳池（约25辆车、21个游泳池）。")
    dota_qa("P3044", "遥感影像 P3044 中有什么目标？", "船只（4艘）。")
    dota_qa("P7799", "遥感影像 P7799 中有什么设施？", "田径场。")

    # Geo
    g = {x["id"]: x for x in geo}
    for pid in ("01_0101", "01_0401", "01_0701"):
        x = g[f"visloc:{pid}"]
        add("geo", f"航拍图像 {pid}.JPG 是在什么位置拍摄的？给出经纬度。",
            f"约北纬{x['lat']:.5f}°、东经{x['lon']:.5f}°。", [x["image"], x["satellite"]],
            [{"source": "geo/uav_visloc/01/01.csv", "computed": f"lat={x['lat']}, lon={x['lon']}"}],
            {"type": "geo", "lat": x["lat"], "lon": x["lon"], "tolerance_m": 100})
    add("geo", "Palos Verdes 这段无人机视频是用哪款无人机拍摄的？", f"{dji['drone_model']}。", [dji["video"]],
        [{"source": dji["telemetry"], "computed": f"飞行记录机型 {dji['drone_model']}"}])
    add("geo", "drone_palos_verdes 视频的起飞点在哪里？",
        f"美国加利福尼亚州帕洛斯弗迪斯（Palos Verdes）海岸，约北纬{dji['home']['lat']:.4f}°、西经{-dji['home']['lon']:.4f}°。",
        [dji["video"]], [{"source": dji["telemetry"], "computed": f"首个有效 GPS 点 {dji['home']}"}],
        {"type": "geo", "lat": dji["home"]["lat"], "lon": dji["home"]["lon"], "tolerance_m": 1000})
    add("geo", "drone_palos_verdes 视频是哪天拍摄的？",
        "2025年2月17日傍晚（美国太平洋时间），对应 UTC 时间 2025年2月18日 01:46 左右。", [dji["video"]],
        [{"source": dji["telemetry"], "computed": f"飞行记录起止 {dji['track_start_utc']} ~ {dji['track_end_utc']}"}])

    # Cross-source
    h_ft = dji["max_height_ft"]
    h_m = h_ft * 0.3048
    over = "超过" if h_ft > 400 else "没有超过"
    add("cross", "按美国 Part 107 的高度限制，drone_palos_verdes 这次飞行的最大高度是否超限？",
        f"{over}。飞行记录中相对起飞点的最大高度为{h_ft:.0f}英尺，Part 107 上限为距地面400英尺。",
        [dji["video"], CFR], [{"source": dji["telemetry"], "computed": f"OSD.height 最大值 {h_ft} ft"},
                              {"source": CFR, "quote": "cannot be higher than 400 feet above ground level"}])
    add("cross", "如果在中国，drone_palos_verdes 这次飞行的最大高度是否进入了真高120米以上的管制空域？",
        f"{'进入了' if h_m > 120 else '没有进入'}。最大高度约{h_ft:.0f}英尺，约合{h_m:.1f}米，"
        f"{'高于' if h_m > 120 else '略低于'}120米（按飞行记录中相对起飞点的高度计算）。",
        [dji["video"], CN], [{"source": dji["telemetry"], "computed": f"{h_ft} ft = {h_m:.1f} m"},
                             {"source": CN, "quote": "真高120米以上空域"}])
    add("cross", "视频 M0202 是夜间拍摄的。如果在美国按 Part 107 进行这样的夜间飞行，无人机需要满足什么灯光要求？",
        "需要点亮防撞灯，可见距离至少3法定英里，闪烁频率足以避免碰撞。", [m2["video"], CFR],
        [{"source": "video/uavdt/M_attr", "computed": f"M0202 属性 {m2['attributes']}"},
         {"source": CFR, "quote": "anti-collision lighting visible for at least 3 statute miles"}])
    add("cross", "英国 PDRA01 和美国 Part 107 规定的最大飞行高度分别是多少？是否一致？",
        "英国 PDRA01 为距地表120米（400英尺），美国 Part 107 为距地面400英尺，两者基本一致。", [UK, CFR],
        [{"source": UK, "quote": "Maximum height not to exceed 120 metres or 400 feet above the surface"},
         {"source": CFR, "quote": "cannot be higher than 400 feet above ground level"}])
    return qa


def write_jsonl(name: str, rows: list[dict]) -> None:
    with open(OUT / name, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"  {name}: {len(rows)}")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    vids = build_videos()
    dets = build_det_images()
    dota = build_dota()
    geo, dji = build_geo()
    texts = {p: doc_text(p, k) for p, k in DOCS.items()}

    json.dump(build_corpus(vids, dets, dota, geo, dji), open(OUT / "corpus.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    write_jsonl("keyframe_gt.jsonl", vids)
    write_jsonl("detection_gt.jsonl", dets)
    write_jsonl("geo_gt.jsonl", geo + [dji])
    queries = build_queries(vids, dets, dota, geo, dji, texts)
    qa = build_qa(vids, dets, dota, geo, dji, texts)
    write_jsonl("queries.jsonl", queries)
    write_jsonl("qa_pairs.jsonl", qa)

    empty = [q["id"] for q in queries if not q["relevant"]]
    if empty:
        raise SystemExit(f"queries without relevant assets: {empty}")
    if len(queries) != 50 or len(qa) != 50:
        raise SystemExit(f"expected 50 queries and 50 QA pairs, got {len(queries)} and {len(qa)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
