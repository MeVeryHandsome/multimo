#!/usr/bin/env python3
"""Generate docs/API.md from the backend's OpenAPI document.

    python3 scripts/gen_api_docs.py                         # from http://127.0.0.1:8000/api.json
    python3 scripts/gen_api_docs.py --source api.json       # from a saved file
    python3 scripts/gen_api_docs.py --out docs/API.md

The OpenAPI document is produced by GoFrame from the request/response
structs in multimo-backend/api, so this file never drifts from the code:
regenerate it after changing an endpoint.
"""
import argparse
import json
import sys
import urllib.request
from collections import OrderedDict

TAGS = OrderedDict([
    ("System", "系统"),
    ("Asset", "素材"),
    ("Keyframe", "关键帧、轨迹与地图"),
    ("Task", "处理任务"),
    ("Search", "检索"),
    ("QA", "问答"),
    ("Testsuite", "评测"),
])

EXAMPLES = {
    ("post", "/api/v1/assets"): """curl -X POST localhost:8000/api/v1/assets \\
  -F file=@flight.mp4 -F sidecar=@flight_record.csv -F collection=survey""",
    ("post", "/api/v1/assets/register"): """curl -X POST localhost:8000/api/v1/assets/register -H 'Content-Type: application/json' -d '{
  "items": [
    {"path": "video/dji/palos_verdes/drone_palos_verdes_20250217174636.mp4", "collection": "dji",
     "sidecar": "video/dji/palos_verdes/flight_record.csv"},
    {"path": "geo/uav_visloc/01/drone/01_0001.JPG", "collection": "visloc",
     "metadata": {"time": "2018-09-17T12:44:21", "geo_reference": "basemap"}}
  ]}'""",
    ("post", "/api/v1/search"): """curl -X POST localhost:8000/api/v1/search -H 'Content-Type: application/json' -d '{
  "query": "以北纬29.7253°、东经115.9748°为中心，半径300米内拍摄的航拍图像", "topK": 10}'""",
    ("post", "/api/v1/qa"): """curl -X POST localhost:8000/api/v1/qa -H 'Content-Type: application/json' -d '{
  "question": "Palos Verdes 那段飞行的最高高度是多少？是否超过美国 Part 107 的限制？"}'""",
    ("post", "/api/v1/testsuite/run"): """curl -X POST localhost:8000/api/v1/testsuite/run -H 'Content-Type: application/json' -d '{"sections": ["retrieval", "qa"]}'""",
    ("post", "/api/v1/pipeline/rerun"): """curl -X POST localhost:8000/api/v1/pipeline/rerun -H 'Content-Type: application/json' -d '{"stage": "analyze", "type": "image"}'""",
}


def load(source):
    if source.startswith("http"):
        with urllib.request.urlopen(source, timeout=20) as r:
            return json.load(r)
    with open(source, encoding="utf-8") as f:
        return json.load(f)


class Doc:
    def __init__(self, spec):
        self.spec = spec
        self.schemas = spec.get("components", {}).get("schemas", {})
        self.names = self._short_names()
        self.used = OrderedDict()  # data structures referenced by endpoints

    def _short_names(self):
        by_short = {}
        for full in self.schemas:
            by_short.setdefault(full.split(".")[-1], []).append(full)
        names = {}
        for short, fulls in by_short.items():
            for full in fulls:
                names[full] = short if len(fulls) == 1 else ".".join(full.split(".")[-2:])
        return names

    @staticmethod
    def ref_name(ref):
        return ref.split("/")[-1]

    def is_envelope(self, full):
        last = full.split(".")[-1]
        return last.endswith("Req") or last.endswith("Res")

    def type_of(self, prop):
        if "$ref" in prop:
            full = self.ref_name(prop["$ref"])
            return self.link(full)
        if prop.get("type") == "array":
            items = prop.get("items", {})
            if "$ref" in items:
                return self.link(self.ref_name(items["$ref"])) + "[]"
            return (items.get("type") or "any") + "[]"
        fmt = prop.get("format")
        t = prop.get("type") or "any"
        if t == "object" and not prop.get("properties"):
            return "object"
        return t

    def link(self, full):
        name = self.names.get(full, full)
        if full in self.schemas and not self.is_envelope(full) and full != "interface":
            self.used.setdefault(full, None)
            return f"[{name}](#{anchor(name)})"
        return name

    def fields(self, schema, required=()):
        rows = []
        for name, prop in (schema.get("properties") or {}).items():
            desc = (prop.get("description") or "").replace("|", "\\|").replace("\n", " ")
            extra = []
            if "default" in prop:
                extra.append(f"默认 `{prop['default']}`")
            if "enum" in prop:
                extra.append("可选 " + " / ".join(f"`{v}`" for v in prop["enum"]))
            if "maximum" in prop:
                extra.append(f"最大 {prop['maximum']}")
            desc = "；".join([d for d in [desc] + extra if d])
            rows.append((name, self.type_of(prop), "是" if name in required else "", desc))
        return rows

    def resolve(self, schema):
        if "$ref" in schema:
            return self.schemas.get(self.ref_name(schema["$ref"]), {}), self.ref_name(schema["$ref"])
        return schema, None


def anchor(name):
    return name.lower().replace(".", "")


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join([" :--- "] * len(head)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(f"`{c}`" if i == 0 and c else (c or "") for i, c in enumerate(r)) + " |")
    return "\n".join(out)


def render(spec):
    d = Doc(spec)
    info = spec.get("info", {})
    ops = OrderedDict((t, []) for t in TAGS)
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            if method not in ("get", "post", "put", "delete", "patch"):
                continue
            tag = (op.get("tags") or ["其他"])[0]
            ops.setdefault(tag, []).append((path, method, op))

    lines = [
        "# Multimo 接口文档",
        "",
        f"> 由 `scripts/gen_api_docs.py` 根据后端的 OpenAPI 文档（`/api.json`，版本 {info.get('version', '')}）生成，"
        "接口改动后重新生成即可。交互式文档：服务启动后访问 `/swagger`。",
        "",
        "## 约定",
        "",
        "- 所有接口以 `/api/v1` 开头，`docker compose` 部署时后端在 `http://localhost:8000`，前端的 `/api` 也会转发到这里。",
        "- 请求和响应都是 JSON（上传文件用 `multipart/form-data`，图片接口直接返回 JPEG）。",
        "- 响应统一包在 `{\"code\": 0, \"message\": \"OK\", \"data\": {...}}` 中，`code` 不为 0 表示失败，`message` 说明原因；下文“返回”列出的是 `data` 的字段。",
        "- 坐标为 WGS84 经纬度（度），距离为米，时间为 ISO 8601 或 `YYYY-MM-DD HH:mm:ss`。",
        "- 服务本身不做鉴权，按内网服务部署；模型密钥只保存在后端的环境变量中。",
        "",
        "## 目录",
        "",
    ]
    for tag, items in ops.items():
        if not items:
            continue
        lines.append(f"- {TAGS.get(tag, tag)}")
        for path, method, op in items:
            title = f"{method.upper()} {path}"
            lines.append(f"  - [{op.get('summary', '')}](#{anchor_op(method, path)})：`{title}`")
    lines.append("- [数据结构](#数据结构)")
    lines.append("")

    for tag, items in ops.items():
        if not items:
            continue
        lines += [f"## {TAGS.get(tag, tag)}", ""]
        for path, method, op in items:
            lines += endpoint(d, path, method, op)

    lines += ["## 数据结构", ""]
    done = set()
    while True:
        pending = [k for k in d.used if k not in done]
        if not pending:
            break
        for full in pending:
            done.add(full)
            s = d.schemas[full]
            name = d.names.get(full, full)
            lines += [f"### {name}", ""]
            if s.get("description"):
                lines += [s["description"], ""]
            rows = [(a, b, c) for a, b, _, c in d.fields(s)]
            lines += [table(["字段", "类型", "说明"], rows) if rows else "（无字段）", ""]
    return "\n".join(lines).rstrip() + "\n"


def anchor_op(method, path):
    return f"{method}-{path}".lower().replace("/", "").replace("{", "").replace("}", "")


def endpoint(d, path, method, op):
    lines = [f'<a id="{anchor_op(method, path)}"></a>', f"### {op.get('summary', '')}", "", f"`{method.upper()} {path}`", ""]
    if op.get("description"):
        lines += [op["description"], ""]
    params = [p for p in op.get("parameters", [])]
    if params:
        rows = []
        for p in params:
            sch = p.get("schema", {})
            where = {"path": "路径", "query": "查询"}.get(p.get("in"), p.get("in"))
            desc = (p.get("description") or "").replace("|", "\\|")
            if "default" in sch:
                desc = "；".join([x for x in [desc, f"默认 `{sch['default']}`"] if x])
            rows.append((p["name"], f"{where}，{d.type_of(sch)}", "是" if p.get("required") else "", desc))
        lines += ["**参数**", "", table(["名称", "位置与类型", "必填", "说明"], rows), ""]
    body = op.get("requestBody", {}).get("content", {})
    for mime, content in body.items():
        schema, _ = d.resolve(content.get("schema", {}))
        rows = d.fields(schema, schema.get("required", []))
        path_names = {p["name"] for p in params if p.get("in") == "path"}
        rows = [r for r in rows if r[0] not in path_names]
        if rows:
            label = "**请求体**（表单）" if "multipart" in mime else "**请求体**"
            lines += [label, "", table(["字段", "类型", "必填", "说明"], rows), ""]
    resp = op.get("responses", {}).get("200", {}).get("content", {})
    for mime, content in resp.items():
        schema, _ = d.resolve(content.get("schema", {}))
        rows = d.fields(schema)
        if rows:
            lines += ["**返回**", "", table(["字段", "类型", "说明"], [(a, b, c) for a, b, _, c in rows]), ""]
        elif "image" in (op.get("description", "") + op.get("summary", "")) or "JPEG" in op.get("description", ""):
            lines += ["**返回**：JPEG 图片。", ""]
    ex = EXAMPLES.get((method, path))
    if ex:
        lines += ["**示例**", "", "```bash", ex, "```", ""]
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="http://127.0.0.1:8000/api.json")
    ap.add_argument("--out", default="docs/API.md")
    a = ap.parse_args()
    md = render(load(a.source))
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"wrote {a.out} ({len(md.splitlines())} lines)", file=sys.stderr)


if __name__ == "__main__":
    main()
