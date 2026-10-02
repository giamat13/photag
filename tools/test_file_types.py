"""Test: the newly recognized file types import correctly end to end (not just "the extension is in a set").

    py -3.12 tools/test_file_types.py

- AVIF (.avif/.avifs): decoded natively by this Pillow build, like HEIC -> a real file is imported, thumbnailed and shown.
- Extra video containers (.wmv, .mts/.m2ts, .ts, .mpg/.mpeg, .m4v, .flv): already compressible via HandBrake
  (compress.VIDEO_OK) but were missing from images.VIDEO_EXT, so the importer never recognized them as media at all.
  Real ffmpeg-encoded fixtures (tools/sandbox/filetypes/) are imported and thumbnailed.
- .gpr (GoPro RAW): added to RAW_EXT; the generic embedded-JPEG-preview scan (images.raw_preview_bytes) is
  extension-agnostic, so this only checks the extension is routed through that same code path.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8784
APP = f"http://127.0.0.1:{PORT}"
FIX = ROOT / "tools" / "sandbox" / "filetypes"
tmp = Path(tempfile.mkdtemp(prefix="photag_filetypes_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8"}
for d in ("appdata", "local", "home", "lib"):
    (tmp / d).mkdir()
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def call(m, p, b=None):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=120) as x:
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait(name):
    time.sleep(0.3)
    while (j := call("GET", f"/api/job/{name}")[1])["state"] not in ("done", "error", "idle"):
        time.sleep(0.2)
    return j


# ---- unit-level: the recognition sets themselves (no server needed)
sys.path.insert(0, str(ROOT))
from app import images  # noqa: E402

for ext in (".avif", ".avifs"):
    check(f"{ext} is a recognized image type", ext in images.IMAGE_EXT)
for ext in (".wmv", ".mpg", ".mpeg", ".mts", ".m2ts", ".ts", ".flv", ".m4v"):
    check(f"{ext} is a recognized video type (matches compress.VIDEO_OK)", ext in images.VIDEO_EXT)
check(".gpr (GoPro RAW) is a recognized RAW type", ".gpr" in images.RAW_EXT and ".gpr" in images.IMAGE_EXT)
try:
    from app import compress
    missing = (compress.VIDEO_OK - {".mov", ".avi", ".webm", ".3gp", ".mkv", ".mp4"}) - images.VIDEO_EXT
    check("every HandBrake-compressible video extension is also importable (no silent gap)", not missing, missing)
finally:
    pass

if not (FIX / "sample.avif").is_file() or not (FIX / "sample.wmv").is_file() or not (FIX / "sample.mts").is_file():
    print("SKIP  end-to-end import (tools/sandbox/filetypes/ fixtures missing)")
    print(f"\n{sum(res)}/{len(res)} passed")
    sys.exit(0 if all(res) else 1)

srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(60):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    call("POST", "/api/settings/library", {"path": str(tmp / "lib")})

    files = [str(FIX / "sample.avif"), str(FIX / "sample.wmv"), str(FIX / "sample.mts")]
    call("POST", "/api/import-folder", {"paths": files, "keywords": [], "album": None})
    j = wait("import")
    ph = {p["filename"]: p for p in call("GET", "/api/photos?limit=99")[1]}
    check("all three real files (AVIF image, WMV and MTS video) import without error", j["state"] == "done" and len(ph) == 3, (j.get("error"), list(ph)))

    av = ph.get("sample.avif")
    check("the AVIF file is stored as an image, not a video", av and not av["is_video"], av)
    check("the AVIF file has real decoded dimensions (not a 0x0 placeholder)", av and av["width"] and av["height"], av)
    if av:
        with urllib.request.urlopen(f"{APP}/thumb/{av['id']}") as r:
            body = r.read()
        check("the AVIF file got a real thumbnail (decoded, not a failure placeholder)", r.status == 200 and len(body) > 200, len(body))

    for name in ("sample.wmv", "sample.mts"):
        v = ph.get(name)
        check(f"{name} is stored as a video", v and v["is_video"], v)
        if v:
            with urllib.request.urlopen(f"{APP}/thumb/{v['id']}") as r:
                body = r.read()
            check(f"{name} got a real thumbnail via ffmpeg (a frame was decoded)", r.status == 200 and len(body) > 200, len(body))

    # the map / grid / folder queries must not choke on the new types (is_video only ever 0 or 1, width/height never None in a way that breaks math)
    check("the photo list query returns clean rows for all the new types", all(isinstance(p["is_video"], int) for p in ph.values()))
finally:
    srv.terminate()
    try:
        srv.wait(timeout=15)
    except Exception:
        srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
