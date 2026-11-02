#!/usr/bin/env python3
"""Download and verify Multimo test data described by testdata/manifest.tsv.

Standard library only. Called by scripts/prepare_testdata.sh; can also be run
directly:

    python3 scripts/lib/testdata.py fetch  --profile mini
    python3 scripts/lib/testdata.py verify --profile mini
    python3 scripts/lib/testdata.py checksum --only dji_flights_full   # maintainers

Manifest columns (tab separated, '#' starts a comment):

    id  profile  kind  dest  md5  bytes  select  strip  urls  license

profile  mini | full | both
kind     file    one file; select=unzip extracts it into dest's directory
         zipsel  selected members of a remote zip, fetched with HTTP Range
                 requests so the whole archive is never downloaded
         multi   several files under a base URL (urls end with '/')
dest     path relative to testdata/ (a file for kind=file, else a directory)
md5      file MD5 for kind=file; for zipsel/multi a "tree MD5": the MD5 of
         the sorted lines "<md5>  <relative path>\\n" of every selected file
select   comma separated paths or prefixes; {0001..0817} and {1..99..10}
         ranges are expanded like bash brace ranges, keeping zero padding
strip    number of leading path components removed from zip member names
urls     mirrors separated by '|', tried in order
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

CHUNK = 2 << 20  # range-request granularity for zipsel
COLUMNS = ["id", "profile", "kind", "dest", "md5", "bytes", "select", "strip", "urls", "license"]


# --------------------------------------------------------------------------- manifest

@dataclass
class Row:
    id: str
    profile: str
    kind: str
    dest: str
    md5: str
    bytes: str
    select: str
    strip: str
    urls: list[str]
    license: str
    line_no: int

    def in_profile(self, profile: str) -> bool:
        return self.profile == "both" or self.profile == profile


def read_manifest(path: Path) -> tuple[list[str], list[Row]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    rows = []
    for i, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cols = line.split("\t")
        if len(cols) != len(COLUMNS):
            sys.exit(f"manifest line {i + 1}: expected {len(COLUMNS)} columns, got {len(cols)}")
        d = dict(zip(COLUMNS, cols))
        d["urls"] = [u for u in d["urls"].split("|") if u]
        rows.append(Row(line_no=i, **d))
    return lines, rows


def write_back(path: Path, lines: list[str], row: Row, md5: str, size: int) -> None:
    cols = lines[row.line_no].split("\t")
    cols[COLUMNS.index("md5")] = md5
    cols[COLUMNS.index("bytes")] = str(size)
    lines[row.line_no] = "\t".join(cols)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


_RANGE = re.compile(r"\{(\d+)\.\.(\d+)(?:\.\.(\d+))?\}")


def expand(select: str) -> list[str]:
    out: list[str] = []
    for item in (s.strip() for s in select.split(",")):
        if not item or item == "-":
            continue
        m = _RANGE.search(item)
        if not m:
            out.append(item)
            continue
        a, b, step = m.group(1), m.group(2), int(m.group(3) or 1)
        width = len(a) if a.startswith("0") else 0
        for n in range(int(a), int(b) + 1, step):
            out.extend(expand(item[: m.start()] + str(n).zfill(width) + item[m.end():]))
    return out


# --------------------------------------------------------------------------- hashing

def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def tree_md5(root: Path, rels: list[str]) -> tuple[str, int]:
    h, total = hashlib.md5(), 0
    for rel in sorted(rels):
        p = root / rel
        h.update(f"{md5_file(p)}  {rel}\n".encode())
        total += p.stat().st_size
    return h.hexdigest(), total


# --------------------------------------------------------------------------- HTTP

def log(msg: str) -> None:
    print(msg, flush=True)


def urlopen(req: urllib.request.Request, timeout: int = 120, tries: int = 4):
    delay = 2
    for attempt in range(tries):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404) or attempt == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == tries - 1:
                raise
        time.sleep(delay)
        delay *= 2


class RangeFile:
    """Read-only, seekable view of a remote file backed by HTTP Range requests."""

    def __init__(self, url: str):
        with urlopen(urllib.request.Request(url, method="HEAD"), timeout=60) as r:
            self.url = r.geturl()
            self.size = int(r.headers["Content-Length"])
        self.pos = 0
        self._cache_start, self._cache = -1, b""
        self.fetched = 0

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self.pos = {0: offset, 1: self.pos + offset, 2: self.size + offset}[whence]
        return self.pos

    def _fill(self, start: int, n: int) -> None:
        end = min(start + max(n, CHUNK), self.size) - 1
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={start}-{end}"})
        with urlopen(req) as r:
            if r.status != 206:
                raise OSError(f"server ignored Range request ({r.status})")
            self._cache = r.read()
        self._cache_start = start
        self.fetched += len(self._cache)

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self.size - self.pos
        if n == 0 or self.pos >= self.size:
            return b""
        out = bytearray()
        while n > 0 and self.pos < self.size:
            off = self.pos - self._cache_start
            if not (0 <= off < len(self._cache)):
                self._fill(self.pos, n)
                off = 0
            piece = self._cache[off: off + n]
            out += piece
            self.pos += len(piece)
            n -= len(piece)
        return bytes(out)


def curl_download(url: str, out: Path) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_name(out.name + ".part")
    cmd = ["curl", "-fL", "--retry", "3", "--retry-delay", "2", "--connect-timeout", "30",
           "-C", "-", "-o", str(part), url]
    if not sys.stdout.isatty():
        cmd.insert(1, "-sS")
    rc = subprocess.call(cmd)
    if rc != 0:
        # Keep the partial file so the next run resumes, unless resuming itself
        # failed (33: range error, 36: bad resume): then start clean next time.
        # A partial from another mirror is fine; the MD5 check catches mismatches.
        if rc in (33, 36):
            part.unlink(missing_ok=True)
        return False
    part.replace(out)
    return True


# --------------------------------------------------------------------------- stamps

def stamp_dir(root: Path) -> Path:
    d = root / ".stamps"
    d.mkdir(exist_ok=True)
    return d


def save_stamp(root: Path, row: Row, md5: str, files: list[str]) -> None:
    (stamp_dir(root) / f"{row.id}.json").write_text(
        json.dumps({"md5": md5, "dest": row.dest, "files": sorted(files)}, ensure_ascii=False))


def load_stamp(root: Path, row: Row) -> dict | None:
    p = root / ".stamps" / f"{row.id}.json"
    return json.loads(p.read_text()) if p.exists() else None


# --------------------------------------------------------------------------- kinds

def strip_name(name: str, strip: int) -> str | None:
    parts = name.split("/")
    return "/".join(parts[strip:]) if len(parts) > strip else None


def matches(rel: str, wanted: list[str]) -> bool:
    return any(rel == w or rel.startswith(w.rstrip("/") + "/") for w in wanted)


def extract_zip(archive: Path, dest: Path, strip: int) -> list[str]:
    files = []
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            rel = strip_name(info.filename, strip)
            if not rel or info.is_dir() or "__MACOSX" in info.filename or rel.endswith(".DS_Store"):
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            files.append(rel)
    return files


def do_file(root: Path, row: Row, keep_archives: bool) -> tuple[str, int, list[str]]:
    out = root / row.dest
    unzip = row.select == "unzip"
    stamp = load_stamp(root, row)
    if unzip and stamp and stamp["md5"] == row.md5 and not out.exists():
        # Archive already extracted and removed earlier.
        return row.md5, int(row.bytes or 0), stamp["files"]
    if out.exists() and row.md5 not in ("", "TBD") and md5_file(out) != row.md5:
        log(f"  {row.dest}: checksum mismatch, downloading again")
        out.unlink()
    if not out.exists():
        for url in row.urls:
            log(f"  GET {url}")
            if curl_download(url, out):
                if row.md5 in ("", "TBD") or md5_file(out) == row.md5:
                    break
                log("  checksum mismatch from this mirror, trying next")
                out.unlink()
        else:
            raise RuntimeError(f"{row.id}: all mirrors failed")
    md5, size = md5_file(out), out.stat().st_size
    files = [row.dest]
    if unzip:
        files = extract_zip(out, out.parent, int(row.strip or 0))
        if not keep_archives:
            out.unlink()
    return md5, size, files


def do_zipsel(root: Path, row: Row) -> tuple[str, int, list[str]]:
    dest, wanted, strip = root / row.dest, expand(row.select), int(row.strip or 0)
    last_err: Exception | None = None
    for url in row.urls:
        try:
            log(f"  ZIP {url}")
            rf = RangeFile(url)
            z = zipfile.ZipFile(rf)  # type: ignore[arg-type]
            picked = []
            for info in z.infolist():
                rel = strip_name(info.filename, strip)
                if rel and not info.is_dir() and matches(rel, wanted):
                    picked.append((info, rel))
            if not picked:
                raise RuntimeError("no members matched the selection")
            missing = [w for w in wanted if not any(matches(rel, [w]) for _, rel in picked)]
            if missing:
                raise RuntimeError(f"selection not found in archive: {missing[:5]}")
            # Members of one directory sit next to each other; reading them in
            # archive order keeps the range cache warm.
            picked.sort(key=lambda p: p[0].header_offset)
            for info, rel in picked:
                target = dest / rel
                if target.exists() and target.stat().st_size == info.file_size:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name(target.name + ".part")
                with z.open(info) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                tmp.replace(target)
            log(f"  {len(picked)} members, {rf.fetched / 1e6:.1f} MB transferred")
            rels = [rel for _, rel in picked]
            md5, size = tree_md5(dest, rels)
            return md5, size, [str(Path(row.dest) / r) for r in rels]
        except Exception as e:  # try the next mirror
            last_err = e
            log(f"  mirror failed: {e}")
    raise RuntimeError(f"{row.id}: all mirrors failed ({last_err})")


def do_multi(root: Path, row: Row) -> tuple[str, int, list[str]]:
    dest, rels = root / row.dest, expand(row.select)
    for rel in rels:
        target = dest / rel
        if target.exists():
            continue
        for base in row.urls:
            url = base + urllib.request.quote(rel)
            if curl_download(url, target):
                break
        else:
            raise RuntimeError(f"{row.id}: could not fetch {rel}")
    md5, size = tree_md5(dest, rels)
    return md5, size, [str(Path(row.dest) / r) for r in rels]


def stream_md5(url: str) -> tuple[str, int]:
    h, n = hashlib.md5(), 0
    with urlopen(urllib.request.Request(url), timeout=300) as r:
        for block in iter(lambda: r.read(1 << 20), b""):
            h.update(block)
            n += len(block)
    return h.hexdigest(), n


# --------------------------------------------------------------------------- commands

def select_rows(rows: list[Row], args) -> list[Row]:
    only = set(args.only.split(",")) if args.only else None
    return [r for r in rows if (only and r.id in only) or (not only and r.in_profile(args.profile))]


def cmd_fetch(args, root: Path, manifest: Path) -> int:
    lines, rows = read_manifest(manifest)
    failed = []
    for row in select_rows(rows, args):
        log(f"[{row.id}] {row.kind} -> {row.dest}")
        try:
            if row.kind == "file":
                md5, size, files = do_file(root, row, args.keep_archives)
            elif row.kind == "zipsel":
                md5, size, files = do_zipsel(root, row)
            elif row.kind == "multi":
                md5, size, files = do_multi(root, row)
            else:
                raise RuntimeError(f"unknown kind {row.kind}")
        except Exception as e:
            log(f"  FAILED: {e}")
            failed.append(row.id)
            continue
        if row.md5 in ("", "TBD"):
            if args.update_manifest:
                write_back(manifest, lines, row, md5, size)
                log(f"  recorded md5 {md5}")
            else:
                log(f"  no checksum in manifest (md5 {md5}); run with --update-manifest to record it")
        elif md5 != row.md5:
            log(f"  FAILED: md5 {md5} != manifest {row.md5}")
            failed.append(row.id)
            continue
        save_stamp(root, row, md5, files)
        log(f"  ok  {size / 1e6:.1f} MB")
    if failed:
        log(f"\nfailed: {', '.join(failed)}")
        return 1
    return 0


def cmd_verify(args, root: Path, manifest: Path) -> int:
    _, rows = read_manifest(manifest)
    bad = 0
    for row in select_rows(rows, args):
        stamp = load_stamp(root, row)
        status = "ok"
        if not stamp:
            status = "missing"
        elif row.kind == "file" and row.select != "unzip":
            p = root / row.dest
            status = "ok" if p.exists() and md5_file(p) == row.md5 else "mismatch"
        elif row.kind == "file":
            p = root / row.dest
            if p.exists() and md5_file(p) != row.md5:
                status = "mismatch"
            elif stamp["md5"] != row.md5 or not all((p.parent / f).exists() for f in stamp["files"]):
                status = "mismatch"
        else:
            rels = [str(Path(f).relative_to(row.dest)) for f in stamp["files"]]
            if not all((root / row.dest / r).exists() for r in rels):
                status = "missing files"
            elif tree_md5(root / row.dest, rels)[0] != row.md5:
                status = "mismatch"
        bad += status != "ok"
        log(f"  {status:<14} {row.id:<26} {row.dest}")
    return 1 if bad else 0


def cmd_checksum(args, root: Path, manifest: Path) -> int:
    """Record checksums by streaming (nothing is written to disk)."""
    lines, rows = read_manifest(manifest)
    for row in select_rows(rows, args):
        if row.kind == "file":
            md5, size = stream_md5(row.urls[0])
        elif row.kind == "multi":
            h, size = hashlib.md5(), 0
            for rel in sorted(expand(row.select)):
                m, n = stream_md5(row.urls[0] + urllib.request.quote(rel))
                h.update(f"{m}  {rel}\n".encode())
                size += n
            md5 = h.hexdigest()
        else:
            log(f"[{row.id}] zipsel rows are recorded by 'fetch --update-manifest'")
            continue
        write_back(manifest, lines, row, md5, size)
        log(f"[{row.id}] {md5}  {size / 1e6:.1f} MB")
    return 0


def main() -> int:
    here = Path(__file__).resolve().parent
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["fetch", "verify", "checksum"])
    p.add_argument("--root", default=os.environ.get("TESTDATA_DIR", str(here.parent.parent / "testdata")))
    p.add_argument("--manifest", default=None)
    p.add_argument("--profile", choices=["mini", "full"], default="mini")
    p.add_argument("--only", default="", help="comma separated row ids (overrides --profile)")
    p.add_argument("--keep-archives", action="store_true")
    p.add_argument("--update-manifest", action="store_true", help="write computed checksums for TBD rows")
    args = p.parse_args()
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest = Path(args.manifest) if args.manifest else root / "manifest.tsv"
    return {"fetch": cmd_fetch, "verify": cmd_verify, "checksum": cmd_checksum}[args.command](args, root, manifest)


if __name__ == "__main__":
    sys.exit(main())
