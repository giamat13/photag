"""Test: photo merge through the server (POST /api/merge): HDR and panorama of imported photos become new photos in the library.

    py -3.12 tools/test_merge_api.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
PORT = 8803
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_merge_api_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"), "HOME": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999", "PHOTAG_EXIF_DELAY": "9999", "PHOTAG_MIGRATE_DELAY": "9999"}
for d in ("appdata", "local", "home", "lib", "src"):
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


def job(name, secs=120):
    t0 = time.time()
    while time.time() - t0 < secs:
        j = call("GET", f"/api/job/{name}")
        if j["state"] in ("done", "error"):
            return j
        time.sleep(0.3)
    return {"state": "timeout"}


rng = np.random.default_rng(5)
base = rng.random((60, 190, 3)).astype(np.float32)
S = np.asarray(Image.fromarray((base * 255).astype(np.uint8)).resize((1520, 480), Image.BICUBIC), dtype=np.float32) / 255.0
S = np.clip(S * 0.85 + rng.random((480, 1520, 1)).astype(np.float32) * 0.15, 0, 1)
for i, x in enumerate((0, 330, 660)):
    Image.fromarray((np.clip(S[i * 8:i * 8 + 400, x:x + 500] * 255, 0, 255)).astype("uint8")).save(tmp / "src" / f"s{i}.jpg", quality=95)
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(60):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    call("POST", "/api/settings/library", {"path": str(tmp / "lib")})
    call("POST", "/api/import-folder", {"paths": [str(p) for p in sorted((tmp / "src").iterdir())]})
    job("import")
    ph = call("GET", "/api/photos?limit=999")
    ids = [p["id"] for p in sorted(ph, key=lambda p: p["filename"])]
    check("three photos imported", len(ids) == 3)
    check("a bad kind is refused", call("POST", "/api/merge", {"ids": ids, "kind": "x"}, ok=False).get("_status") == 400)
    call("POST", "/api/merge", {"ids": ids, "kind": "pano"})
    j = job("merge")
    check("the panorama job finishes", j["state"] == "done" and j["result"] and j["result"]["id"], j)
    ph2 = call("GET", "/api/photos?limit=999")
    new = [p for p in ph2 if p["id"] not in ids]
    check("...and the panorama is a new photo in the library, wider than a single shot", len(new) == 1 and new[0]["width"] > 1000 and "Pano" in new[0]["filename"], new and (new[0]["filename"], new[0]["width"]))
    call("POST", "/api/merge", {"ids": ids, "kind": "hdr"})
    j = job("merge")
    check("the HDR job finishes", j["state"] == "done", j)
    check("...and adds another photo", len([p for p in call("GET", "/api/photos?limit=999") if p["id"] not in ids]) == 2)
    call("POST", "/api/merge", {"ids": ids[:1], "kind": "hdr"})
    j = job("merge")
    check("one photo alone is refused with a message", j["state"] == "error" and j["error"], j)
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()
n = res.count(False)
print(f"\n{len(res) - n}/{len(res)} passed")
sys.exit(1 if n else 0)
