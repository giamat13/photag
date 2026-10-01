"""Test: live statistics and cancel for imports (the data behind the full import screen).

    py -3.12 tools/test_import_stats.py
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
PORT = 8778
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_import_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8"}
for d in ("appdata", "local", "home", "lib", "src"):
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


def wait(name="import"):
    while (j := call("GET", f"/api/job/{name}")[1])["state"] not in ("done", "error", "idle"):
        time.sleep(0.1)
    return j


# 60 distinct jpegs (a big one plus a few bytes of difference each, so none is a duplicate)
base = (ROOT / "tools" / "sandbox" / "photos" / "big_q97_exif.jpg").read_bytes()
for i in range(60):
    (tmp / "src" / f"p{i:02d}.jpg").write_bytes(base + os.urandom(64))
paths = [str(tmp / "src" / f"p{i:02d}.jpg") for i in range(60)]
total_bytes = sum(Path(p).stat().st_size for p in paths)

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

    # ---- statistics while a folder import runs
    seen = []
    call("POST", "/api/import-folder", {"paths": paths[:30] + [str(tmp / "src" / "nope.jpg")], "keywords": [], "album": None})
    while (j := call("GET", "/api/job/import")[1])["state"] not in ("done", "error", "idle"):
        seen.append(j.get("extra") or {})
        time.sleep(0.05)
    x = j["extra"]
    check("import finished, with statistics attached", j["state"] == "done" and x.get("source") == "folder", x.get("source"))
    check("added = 30, missing = 1 (a path that does not exist), failed = 0", (x["added"], x["missing"], x["failed"]) == (30, 1, 0), (x["added"], x["missing"], x["failed"]))
    check("bytes counted = the files' sizes; the total was known up front", x["bytes"] == sum(Path(p).stat().st_size for p in paths[:30]) and x["bytes_total"] >= x["bytes"], (x["bytes"], x["bytes_total"]))
    check("the missing file is listed by name", "nope.jpg" in x["failures"])
    check("a start time is given (for elapsed time and speed)", abs(x["t0"] - time.time()) < 600)
    grew = [s.get("added", 0) for s in seen if s]
    check("numbers grow while it runs (the screen can show progress)", grew == sorted(grew) and len(set(grew)) > 2, f"{len(set(grew))} distinct values")
    check("the current file name is reported while running", any(s.get("current") for s in seen))

    # ---- the same files again: all duplicates
    call("POST", "/api/import-folder", {"paths": paths[:30], "keywords": [], "album": None})
    x = wait()["extra"]
    check("importing the same files again: 30 'already in catalog', 0 added", (x["added"], x["duplicates"]) == (0, 30), (x["added"], x["duplicates"]))

    # ---- cancel
    call("POST", "/api/import-folder", {"paths": paths, "keywords": [], "album": None})
    while (j := call("GET", "/api/job/import")[1]).get("extra", {}).get("added", 0) + j.get("extra", {}).get("duplicates", 0) < 5 and j["state"] not in ("done", "error"):
        time.sleep(0.02)
    code, _ = call("POST", "/api/import/cancel")
    j = wait()
    x = j["extra"]
    n = x["added"] + x["duplicates"]
    check("cancel stops the import cleanly (state done, message says cancelled)", code == 200 and j["state"] == "done" and "cancel" in j["msg"].lower(), j["msg"])
    check("cancel happened part-way: fewer than all 60 files processed, at least 5", 5 <= n < 60, n)
    ph = call("GET", "/api/photos?limit=99")[1]
    check("what was imported before the cancel is kept (30 + the new ones)", len(ph) >= 30 + max(0, x["added"]), len(ph))
    call("POST", "/api/import-folder", {"paths": paths, "keywords": [], "album": None})
    x = wait()["extra"]
    check("importing again afterwards completes the rest, nothing twice", len(call("GET", "/api/photos?limit=99")[1]) == 60 and x["added"] + x["duplicates"] == 60, (x["added"], x["duplicates"]))
finally:
    srv.terminate()
    try:
        srv.wait(timeout=15)
    except Exception:
        srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
