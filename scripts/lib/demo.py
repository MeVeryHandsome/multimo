#!/usr/bin/env python3
"""HTTP steps of scripts/demo.sh (standard library only).

    demo.py health   URL [timeout_s]
    demo.py register URL CORPUS_JSON
    demo.py wait     URL [timeout_s]
    demo.py showcase URL
    demo.py evaluate URL OUT_DIR
"""
import json
import sys
import time
import urllib.error
import urllib.request


def call(url, path, body=None, timeout=600):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url.rstrip("/") + "/api/v1" + path, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        env = json.load(r)
    if env.get("code") != 0:
        raise RuntimeError(f"{path}: {env.get('message')}")
    return env["data"]


def health(url, timeout=900):
    deadline = time.time() + float(timeout)
    last = ""
    while time.time() < deadline:
        try:
            h = call(url, "/system/health", timeout=5)
            missing = h.get("missing") or []
            print(f"后端 {h['version']}，数据库 {h['database']}，视觉模型 {h['models']['vlm']}，"
                  f"目标检测 {h['models'].get('detector') or '未配置（由视觉模型画框）'}")
            if missing:
                print(f"注意：缺少配置 {', '.join(missing)}，相关功能会降级")
            return 0
        except (urllib.error.URLError, OSError, RuntimeError, KeyError) as e:
            if str(e) != last:
                print(f"等待后端启动……（{e}）")
                last = str(e)
            time.sleep(5)
    print("后端在规定时间内没有就绪，请查看 docker compose logs backend", file=sys.stderr)
    return 1


def register(url, corpus):
    with open(corpus, encoding="utf-8") as f:
        items = json.load(f)["assets"]
    created = existing = failed = 0
    for i in range(0, len(items), 200):
        batch = [{k: v for k, v in it.items() if k in ("path", "collection", "type", "sidecar", "metadata")} for it in items[i:i + 200]]
        res = call(url, "/assets/register", {"items": batch})
        for r in res["results"]:
            if r.get("error"):
                failed += 1
                print(f"  登记失败 {r['path']}：{r['error']}")
            elif r.get("created"):
                created += 1
            else:
                existing += 1
    print(f"评测语料 {len(items)} 个文件：新登记 {created} 个，已存在 {existing} 个，失败 {failed} 个")
    return 1 if failed else 0


def wait(url, timeout=6 * 3600):
    deadline = time.time() + float(timeout)
    started = time.time()
    last_print = 0
    while time.time() < deadline:
        s = call(url, "/system/stats", timeout=10)
        t = {r["key"]: r["count"] for r in s["tasksByStatus"]}
        busy = t.get("pending", 0) + t.get("running", 0)
        if busy == 0:
            print(f"处理完成：{s['keyframes']} 个关键帧，{s['docChunks']} 个文档片段，"
                  f"失败任务 {t.get('failed', 0)} 个，用时 {int(time.time() - started)} 秒")
            return 0
        if time.time() - last_print > 15:
            done = t.get("done", 0)
            print(f"  处理中：已完成 {done} 项，排队 {t.get('pending', 0)} 项，运行 {t.get('running', 0)} 项"
                  f"（{int(time.time() - started)} 秒）")
            last_print = time.time()
        time.sleep(5)
    print("处理没有在规定时间内完成，可稍后再运行评测", file=sys.stderr)
    return 1


def showcase(url):
    searches = [
        "停满小汽车的停车场或道路（40 辆以上）",
        "以北纬29.7253°、东经115.9748°为中心，半径300米内拍摄的航拍图像",
        "微型无人驾驶航空器的空机重量和最大飞行高度",
        "Part 107 night operations anti-collision lighting",
    ]
    for q in searches:
        r = call(url, "/search", {"query": q, "topK": 3})
        print(f"\n检索：{q}")
        for i, x in enumerate(r["results"], 1):
            where = f"第 {x['page']} 页" if x.get("page") else (f"{x['tsSec']:.0f} 秒" if x.get("tsSec") else "")
            print(f"  {i}. {x['filename']} {where}  {x['score']:.2f}  {x['snippet'][:60]}")
    for q in ["Palos Verdes 那段飞行的最高高度是多少？是否超过美国 Part 107 的限制？",
              "遥感影像 P6519 主要拍到的是什么场景？"]:
        r = call(url, "/qa", {"question": q})
        print(f"\n问答：{q}\n  {r['answer'].strip()}")
        for e in r["evidence"][:3]:
            print(f"    [{e['n']}] {e['filename']}")
    return 0


def evaluate(url, out_dir):
    import os
    st = call(url, "/testsuite/run", {})
    run_id = st.get("runId")
    print(f"评测 {run_id} 已开始")
    last = ""
    while True:
        st = call(url, "/testsuite/status", timeout=10)
        if not st.get("running"):
            break
        if st.get("progress") != last:
            last = st.get("progress")
            print(f"  {last}")
        time.sleep(3)
    if st.get("error"):
        print(f"评测失败：{st['error']}", file=sys.stderr)
        return 1
    rep = call(url, f"/testsuite/reports/{run_id}")
    print(f"\n核心指标（{sum(1 for m in rep['metrics'] if m['primary'] and m['pass'])}/"
          f"{sum(1 for m in rep['metrics'] if m['primary'])} 达标）：")
    for m in rep["metrics"]:
        mark = ("达标" if m["pass"] else "未达标") if m["primary"] else "参考"
        print(f"  {mark:<4} {m['name']}：{m['display']}（目标 {m['target']}）")
    os.makedirs(out_dir, exist_ok=True)
    for ext in ("html", "json", "md"):
        path = os.path.join(out_dir, f"test_report.{ext}")
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/v1/testsuite/reports/{run_id}/{ext}", timeout=60) as r, open(path, "wb") as f:
            f.write(r.read())
    print(f"\n报告已保存到 {out_dir}/test_report.html")
    return 0


def main():
    cmd, url, *rest = sys.argv[1:]
    fn = {"health": health, "register": register, "wait": wait, "showcase": showcase, "evaluate": evaluate}[cmd]
    sys.exit(fn(url, *rest))


if __name__ == "__main__":
    main()
