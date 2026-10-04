"""Test: automatic import from a folder. New photos that appear in the chosen folder come in by themselves; what was
already there is skipped (unless asked for); duplicates are not stored twice; files still being written wait; the
folder must be outside the library. Throw-away profile, nothing real is touched.

    py -3.12 tools/test_auto_import.py
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
SAMPLES = ROOT / "tools" / "sandbox" / "photos"
PORT = 8781
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_autoimport_test_"))
for k, v in (("APPDATA", "appdata"), ("LOCALAPPDATA", "local"), ("USERPROFILE", "home")):
    (tmp / v).mkdir()
    os.environ[k] = str(tmp / v)
os.environ.update(PYTHONIOENCODING="utf-8", PHOTAG_NO_OPEN="1", PHOTAG_AUTOIMPORT_TICK="1", PHOTAG_AUTOIMPORT_DELAY="0",
                  PHOTAG_AUTOIMPORT_SETTLE="0", PHOTAG_BACKUP_START_DELAY="9999")
sys.path.insert(0, str(ROOT))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def call(m, p, b=None, expect=200):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(r, timeout=60).read() or b"{}")
    except urllib.error.HTTPError as e:
        if e.code == expect:
            return {"status": e.code}
        raise


def wait_for(fn, secs=30):
    t0 = time.time()
    while time.time() - t0 < secs:
        v = fn()
        if v:
            return v
        time.sleep(0.4)
    return None


def n_photos():
    return len(call("GET", "/api/photos?limit=999"))


# ---- in-process: a file that is still being written (modified a moment ago) waits for the next pass
from app import db, importer  # noqa: E402
from app.config import PATHS  # noqa: E402
from app import config  # noqa: E402

config.set_library_root(tmp / "lib0")
PATHS.refresh()
db.init_db().close()
watch0 = tmp / "watch0"
watch0.mkdir()
shutil.copy2(SAMPLES / "paris.jpg", watch0 / "fresh.jpg")
os.utime(watch0 / "fresh.jpg")                               # copy2 keeps the sample's old date: it must look just written
os.environ["PHOTAG_AUTOIMPORT_SETTLE"] = "3600"           # "settled" = untouched for an hour: the file counts as still being written
r = importer.run_auto_import(str(watch0), importer.Progress())
check("a file that was just modified is not imported yet", r["added"] == 0, r)
os.environ["PHOTAG_AUTOIMPORT_SETTLE"] = "0"
r = importer.run_auto_import(str(watch0), importer.Progress())
check("...but is imported on a later pass", r["added"] == 1, r)
r = importer.run_auto_import(str(watch0), importer.Progress())
check("a pass over an unchanged folder imports nothing and does not re-read files", r == {"added": 0, "duplicates": 0, "failed": 0, "cloud": 0}, r)
(watch0 / "empty.jpg").write_bytes(b"")                    # a file that has just been created and is still empty
r = importer.run_auto_import(str(watch0), importer.Progress())
check("an empty file (a copy that has not started) is left alone", r["added"] == 0 and r["failed"] == 0, r)

# ---- through the server
env = {**os.environ}
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(60):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    lib = tmp / "lib"
    call("POST", "/api/settings/library", {"path": str(lib)})
    watch = tmp / "watch"
    watch.mkdir()
    shutil.copy2(SAMPLES / "jerusalem.jpg", watch / "old1.jpg")
    shutil.copy2(SAMPLES / "nogps.jpg", watch / "old2.jpg")

    r = call("POST", "/api/auto-import", {"enabled": True, "folder": str(lib / "media")}, expect=400)
    check("a folder inside the library is refused", r.get("status") == 400, r)
    r = call("POST", "/api/auto-import", {"enabled": True, "folder": str(tmp / "nope")}, expect=404)
    check("a folder that does not exist is refused", r.get("status") == 404, r)
    check("nothing was switched on by the refused requests", call("GET", "/api/auto-import")["enabled"] is False)

    r = call("POST", "/api/auto-import", {"enabled": True, "folder": str(watch)})
    check("enabled with a folder", r["enabled"] and r["folder"] == str(watch) and r["folder_ok"], r)
    time.sleep(4)
    check("photos that were already in the folder are NOT imported", n_photos() == 0, n_photos())

    shutil.copy2(SAMPLES / "telaviv_1.jpg", watch / "new1.jpg")
    ok = wait_for(lambda: n_photos() == 1)
    check("a new photo arrives -> imported by itself", bool(ok), n_photos())
    bg = call("GET", "/api/background")["auto_import"]
    check("the window is told (seq / count) so it can refresh", bg["seq"] >= 1 and bg["last_added"] == 1 and bg["total_added"] == 1, bg)

    sub = watch / "camera" / "2026"
    sub.mkdir(parents=True)
    shutil.copy2(SAMPLES / "telaviv_1.jpg", sub / "same_content_other_name.jpg")      # a duplicate of what is already in the catalog
    shutil.copy2(SAMPLES / "telaviv_2.jpg", sub / "new2.jpg")
    ok = wait_for(lambda: n_photos() == 2)
    time.sleep(3)
    check("a file in a sub-folder is found; an identical copy is not stored twice", ok and n_photos() == 2, n_photos())

    call("POST", "/api/auto-import", {"enabled": False})
    shutil.copy2(SAMPLES / "paris.jpg", watch / "while_off.jpg")
    time.sleep(4)
    check("switched off: nothing is imported", n_photos() == 2, n_photos())
    call("POST", "/api/auto-import", {"enabled": True})
    ok = wait_for(lambda: n_photos() == 3)
    check("switched on again: what arrived meanwhile comes in (the old files stay skipped)", bool(ok) and n_photos() == 3, n_photos())

    # a second folder, this time with "also import what is already there"
    watch2 = tmp / "watch2"
    watch2.mkdir()
    shutil.copy2(SAMPLES / "big_q97_exif.jpg", watch2 / "existing.jpg")
    call("POST", "/api/auto-import", {"enabled": True, "folder": str(watch2), "existing": True})
    ok = wait_for(lambda: n_photos() == 4)
    check("with 'also import the existing photos', the folder's current files come in", bool(ok), n_photos())
    call("POST", "/api/auto-import", {"enabled": False})
finally:
    srv.terminate()
    try:
        srv.wait(10)
    except Exception:
        srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
