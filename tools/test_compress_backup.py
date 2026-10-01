"""Test: compressing makes a regular backup first (instead of a per-file copy), and the backup can bring the original back.

    py -3.12 tools/test_compress_backup.py
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
PORT = 8780
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_compress_backup_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8"}
for d in ("appdata", "local", "home", "lib", "bk"):
    (tmp / d).mkdir()
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def call(m, p, b=None):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=120) as x:
            return json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"http": e.code, **json.loads(e.read() or b"{}")}


def wait(name):
    time.sleep(0.3)
    t0 = time.time()
    while (j := call("GET", f"/api/job/{name}"))["state"] not in ("done", "error", "idle"):
        time.sleep(0.2)
        if time.time() - t0 > 40:
            print("STUCK", name, j["state"], j.get("msg"), j.get("error"), flush=True)
            sys.exit(2)
    return j


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
    call("POST", "/api/backup/settings", {"folder": str(tmp / "bk"), "include_media": False, "enabled": False})   # even then compression backs up the photos
    src = ROOT / "tools" / "sandbox" / "photos"
    call("POST", "/api/import-folder", {"paths": [str(src / "big_q97_exif.jpg"), str(src / "paris.jpg"), str(src / "jerusalem.jpg")], "keywords": [], "album": None})
    wait("import")
    ph = {p["filename"]: p for p in call("GET", "/api/photos?limit=99")}
    pid = ph["big_q97_exif.jpg"]["id"]
    media = tmp / "lib" / "media"
    f = next(media.rglob("big_q97_exif.jpg"))
    orig_size = f.stat().st_size
    check("no backup exists before compressing", not list((tmp / "bk").glob("photag-*.zip")))

    call("POST", f"/api/photo/{pid}/compress", {"options": {"quality": 60}})
    j = wait("compress")
    check("the photo was compressed", j["state"] == "done" and j["result"].get("applied"), (j["state"], j.get("error"), j["result"].get("failed")))
    new_size = next(media.rglob("big_q97_exif.jpg")).stat().st_size
    check("the file in the library is now smaller", new_size < orig_size, (orig_size, new_size))

    snaps = [m for m in call("GET", "/api/backup")["snapshots"] if m["reason"] == "before-compress"]
    check("a regular backup 'before-compress' was made first", len(snaps) == 1, [m["reason"] for m in call("GET", "/api/backup")["snapshots"]])
    s = snaps[0]
    check("it holds the photo files even though 'include photos' is off in the settings", s.get("media_ok") and s["includes_media"])
    in_bk = next((tmp / "bk" / s["media_dir"]).rglob("big_q97_exif.jpg"))
    check("the backup has the ORIGINAL (uncompressed) version", in_bk.stat().st_size == orig_size, (in_bk.stat().st_size, orig_size))
    check("no per-file copy was left in .originals", not (media / ".originals").exists() or not any((media / ".originals").iterdir()))
    check("no 'previous version' row (the backup replaces it)", call("GET", f"/api/photo/{pid}").get("video_backups", 0) in (0, None, []))

    # a second file: another backup is made again for a new job; a batch makes ONE for all its files
    call("POST", "/api/compress/batch", {"ids": [ph["paris.jpg"]["id"], ph["jerusalem.jpg"]["id"]], "video": {}, "image": {"quality": 60}})
    j = wait("compress")
    n = len([m for m in call("GET", "/api/backup")["snapshots"] if m["reason"] == "before-compress"])
    check("a batch of two files makes one backup, not two", n == 2, n)

    # undo from the backup: catalog + the changed file come back
    r = call("POST", "/api/backup/restore", {"name": s["name"], "media": True, "settings": False, "overwrite": True})
    j = wait("backup")
    check("restoring with 'replace changed files' succeeded", j["state"] == "done", j.get("error"))
    back = next(media.rglob("big_q97_exif.jpg")).stat().st_size
    check("the original file is back in the library", back == orig_size, (back, orig_size))
    check("the catalog agrees (size of the photo)", call("GET", f"/api/photo/{pid}")["bytes"] == orig_size)
    check("a safety backup 'before-restore' holding the photo files was made",
          any(m["reason"] == "before-restore" and m.get("media_ok") for m in call("GET", "/api/backup")["snapshots"]))

    # without the option nothing existing is overwritten
    call("POST", f"/api/photo/{pid}/compress", {"options": {"quality": 50}})
    wait("compress")
    smaller = next(media.rglob("big_q97_exif.jpg")).stat().st_size
    call("POST", "/api/backup/restore", {"name": s["name"], "media": True, "settings": False})
    wait("backup")
    check("restoring WITHOUT the option never overwrites a file that exists", next(media.rglob("big_q97_exif.jpg")).stat().st_size == smaller)

    # a backup that cannot be made (here: another one is running) stops the compression and changes nothing
    call("POST", "/api/backup/settings", {"folder": str(tmp / "bk")})
    (tmp / "bk" / ".backup.lock").write_text(f"{os.getpid()} {time.time()}")        # "another backup is running" (this test process is alive)
    before = next(media.rglob("big_q97_exif.jpg")).stat().st_size
    call("POST", f"/api/photo/{ph['paris.jpg']['id']}/compress", {"options": {"quality": 30}})
    j = wait("compress")
    check("when the backup cannot be made, the compression stops", j["state"] == "error" and "backup" in (j.get("error") or "").lower(), j.get("error"))
    check("...and the file is unchanged", next(media.rglob("big_q97_exif.jpg")).stat().st_size == before)
finally:
    srv.terminate()
    try:
        srv.wait(timeout=15)
    except Exception:
        srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
