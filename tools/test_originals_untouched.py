"""Rule number one: photag never touches a user's ORIGINAL files unless they run an Export. This test fingerprints (content, size,
modified time) the files the user owns -- the folder they import from and the folder of reference mode ("photos stay in my folder") --
and checks after every kind of work that nothing changed: importing, scanning, rating / flagging / keywords / captions, a date and
a place typed into the metadata (also with "write into the file" asked for), dates from file names, rotate, compress, geotagging with
the "also write the place into the JPEG" option, the quality analysis, search, and the Export itself (which only READS the originals).
Throw-away profile.

    py -3.12 tools/test_originals_untouched.py
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8796
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_originals_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999"}
for d in ("appdata", "local", "home", "lib", "from", "own", "out"):
    (tmp / d).mkdir()
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def call(m, p, b=None, ok=True):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(r, timeout=120).read() or b"{}")
    except urllib.error.HTTPError as e:
        if ok:
            raise
        return {"_status": e.code}


def wait_job(name):
    while call("GET", f"/api/job/{name}")["state"] not in ("done", "error", "idle"):
        time.sleep(0.3)


def fingerprint(folder: Path) -> dict:
    out = {}
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(folder))] = (hashlib.sha256(p.read_bytes()).hexdigest(), st.st_size, st.st_mtime_ns)
    return out


# the user's originals: pictures with a date in the name, one with EXIF, a GIF, a PNG
from PIL import Image  # noqa: E402


def make(folder: Path, d: int = 0):
    Image.new("RGB", (300, 200), (200, 60, 60 + d)).save(folder / "IMG-20230514-WA0001.jpg")
    ex = Image.Exif()
    ex[0x0112] = 6
    ex[0x9003 if False else 0x0132] = "2022:03:04 10:20:30"
    Image.new("RGB", (200, 100), (60, 120, 200 - d)).save(folder / "turned.jpg", exif=ex)
    Image.new("RGB", (120, 90), (40, 200 - d, 60)).save(folder / "Screenshot_20240101-101530.png")
    frames = [Image.new("RGB", (80, 60 + d), c) for c in ((200, 40, 40), (40, 200, 60))]
    frames[0].save(folder / "dance.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)


make(tmp / "from")
make(tmp / "own", 9)
FROM0, OWN0 = fingerprint(tmp / "from"), fingerprint(tmp / "own")
time.sleep(1.2)                       # a changed modified time must be visible


def untouched(step: str):
    a, b = fingerprint(tmp / "from"), fingerprint(tmp / "own")
    check(f"{step}: the folder the photos were imported from is unchanged", a == FROM0, [k for k in set(a) | set(FROM0) if a.get(k) != FROM0.get(k)][:3])
    check(f"{step}: the user's own folder (reference mode) is unchanged", b == OWN0, [k for k in set(b) | set(OWN0) if b.get(k) != OWN0.get(k)][:3])


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

    # 1. import (copies into the library) and the reference-mode scan (the photos stay where they are)
    call("POST", "/api/import-folder", {"paths": [str(p) for p in sorted((tmp / "from").iterdir())], "keywords": ["trip"], "album": "Imported"})
    wait_job("import")
    untouched("importing")
    call("POST", "/api/ref", {"enabled": True, "folder": str(tmp / "own")})
    time.sleep(1)
    wait_job("refscan")
    photos = call("GET", "/api/photos?limit=999")
    ext = [p for p in photos if os.path.isabs(call("GET", f"/api/photo/{p['id']}")["rel_path"])]
    lib = [p for p in photos if p not in ext]
    check("both kinds of photos are in the catalog (copies in the library, references to the user's own folder)", len(lib) == 4 and len(ext) >= 3, (len(lib), len(ext)))
    untouched("a reference-mode scan")

    ids = [p["id"] for p in photos]
    ext_ids = [p["id"] for p in ext]
    # 2. everyday work in the catalog
    call("PATCH", "/api/photos", {"ids": ids, "rating": 4, "flag": 1, "label": "red", "favorited": 1, "add_tags": ["sea", "sun"], "description": "a caption"})
    untouched("rating, flag, color, favorite, keywords and a caption")
    call("PATCH", "/api/photos", {"ids": ids, "taken_at": 1700000000, "lat": 32.08, "lng": 34.78, "write_exif": True})
    untouched("a date and a place typed into the metadata (with 'write into the file' asked for)")
    r = call("POST", "/api/dates/from-names", {"apply": True})
    untouched("dates from file names")
    call("POST", "/api/analysis/run", {"eyes": False})
    wait_job("analysis")
    untouched("the quality analysis")
    call("POST", "/api/smart/ids", {"criteria": {"q": "sea", "flags": ["pick"]}})
    untouched("a search")

    # 3. work that could change a file: all of it is refused for the user's own folder, and none of it touches the folder they imported from
    for pid in ext_ids:
        call("POST", f"/api/photo/{pid}/rotate", {"degrees": 90}, ok=False)
        call("POST", f"/api/photo/{pid}/compress", {"options": {"quality": 40}}, ok=False)
    untouched("rotate and compress asked for the photos of the user's own folder")
    call("POST", "/api/geotag/apply", {"items": [{"id": pid, "lat": 31.0, "lng": 35.0} for pid in ext_ids], "write_exif": True}, ok=False)
    untouched("a place applied with 'also write it into the JPEG files'")

    # 4. the Export is the one thing that writes -- to a copy elsewhere; the originals are only read
    call("POST", "/api/export", {"ids": ids, "dest": str(tmp / "out"), "originals": True, "kind": "folder"}, ok=False)
    time.sleep(2)
    untouched("an export")
    check("...and the export wrote its copies to the destination", any((tmp / "out").rglob("*.jpg")) or any((tmp / "out").rglob("*.png")), sorted(str(p.name) for p in (tmp / "out").rglob("*"))[:4])
finally:
    srv.terminate()
sys.exit(0 if all(res) else 1)
