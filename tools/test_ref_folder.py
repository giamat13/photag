"""Test: "photos stay in my folder" (app/refmode.py): photag lists the image files of a folder you already have, keeps the
list up to date, and never writes to that folder -- except that deleting a photo from the trash sends its file to the
Recycle Bin. Throw-away profile; the folder used here is created in a temp directory.

    py -3.12 tools/test_ref_folder.py
"""
import hashlib
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
PORT = 8783
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_ref_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_REF_DELAY": "3600"}
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
            return x.status, json.loads(x.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait(name):
    time.sleep(0.3)
    t0 = time.time()
    while (j := call("GET", f"/api/job/{name}")[1])["state"] not in ("done", "error", "idle"):
        time.sleep(0.2)
        if time.time() - t0 > 120:
            break
    return j


def snapshot(folder: Path) -> dict:
    """Everything in the folder: relative path -> (size, mtime_ns, sha256). Directories too."""
    out = {}
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder).as_posix()
        if p.is_dir():
            out[rel + "/"] = None
        else:
            st = p.stat()
            out[rel] = (st.st_size, st.st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
    return out


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
    call("POST", "/api/backup/settings", {"folder": str(tmp / "bk"), "include_media": True, "enabled": False})

    # ---- the user's own folder, with a structure that is not all photos
    src = ROOT / "tools" / "sandbox" / "photos"
    mine = tmp / "My Pictures"
    for sub in ("2020/sub", "misc", ".hidden", "Documents"):
        (mine / sub).mkdir(parents=True)
    a, b, c = src / "paris.jpg", src / "jerusalem.jpg", src / "telaviv_1.jpg"
    shutil.copy2(a, mine / "2020" / "a.jpg")
    shutil.copy2(b, mine / "2020" / "sub" / "b.jpg")
    shutil.copy2(c, mine / "misc" / "c.jpg")
    shutil.copy2(a, mine / "copy_of_a.jpg")                                   # identical content to a.jpg
    shutil.copy2(src / "test_video.mp4", mine / "misc" / "clip.mp4")
    shutil.copy2(a, mine / ".hidden" / "h.jpg")
    (mine / "Documents" / "notes.txt").write_text("not a photo")
    (mine / "Documents" / "paper.pdf").write_bytes(b"%PDF-1.4 not a photo")
    before = snapshot(mine)

    # ---- off by default
    code, info = call("GET", "/api/ref")
    check("off by default, no folder", code == 200 and info["enabled"] is False and not info["folder"], info)
    check("nothing is listed while it is off", call("GET", "/api/photos?limit=99")[1] == [])

    # ---- folders that must be refused
    check("a folder inside the photag library is refused", call("POST", "/api/ref", {"enabled": True, "folder": str(tmp / "lib" / "media")})[0] in (400, 404))
    check("the library itself is refused", call("POST", "/api/ref", {"enabled": True, "folder": str(tmp / "lib")})[0] == 400)
    check("a folder that does not exist is refused", call("POST", "/api/ref", {"enabled": True, "folder": str(tmp / "nope")})[0] in (400, 404))
    check("...and it is still off", call("GET", "/api/ref")[1]["enabled"] is False)

    # ---- turn on: only the image files are shown, nothing is written to the folder
    code, info = call("POST", "/api/ref", {"enabled": True, "folder": str(mine)})
    j = wait("refscan")
    ph = call("GET", "/api/photos?limit=99")[1]
    names = sorted(p["filename"] for p in ph)
    check("turning it on scans the folder", code == 200 and j["state"] == "done", j.get("error"))
    check("only image files are listed (no videos, documents, hidden folders); an identical copy is listed once",
          names == ["a.jpg", "b.jpg", "c.jpg"], names)
    check("the scan report says what it did", j["result"]["added"] == 3 and j["result"]["duplicates"] == 1, j["result"])
    check("NOTHING in the folder changed (no new files, no changed dates, no new folders)", snapshot(mine) == before)
    code, row = call("GET", f"/api/photo/{ph[0]['id']}")
    check("a photo's path is its real path in your folder", Path(row["rel_path"]).is_absolute() and str(mine).lower() in row["rel_path"].lower(), row["rel_path"])
    pid_b = next(p["id"] for p in ph if p["filename"] == "b.jpg")
    pid_c = next(p["id"] for p in ph if p["filename"] == "c.jpg")
    pid_a = next(p["id"] for p in ph if p["filename"] == "a.jpg")
    with urllib.request.urlopen(f"{APP}/thumb/{pid_a}") as r:
        check("thumbnails work (kept in the library, not in your folder)", r.status == 200 and len(r.read()) > 500)
    with urllib.request.urlopen(f"{APP}/media/{pid_a}") as r:
        check("the photo itself is served from your folder", r.status == 200 and len(r.read()) == (mine / "2020" / "a.jpg").stat().st_size)
    folders = [f["name"].replace("\\", "/").lower() for f in call("GET", "/api/folders")[1]["folders"]]
    check("the folder list shows your own folders", any(f.endswith("/2020/sub") for f in folders) and any(f.endswith("/misc") for f in folders), folders)
    code, fl = call("GET", "/api/photos?limit=99&folder=" + urllib.request.quote(str(mine / "2020" / "sub").replace("\\", "/")))
    check("opening one of your folders shows its photos", [p["filename"] for p in fl] == ["b.jpg"], fl)

    # ---- the folder changes: new file, moved file (keeps rating), removed file
    call("PATCH", f"/api/photo/{pid_b}", {"rating": 5})
    shutil.copy2(src / "nogps.jpg", mine / "misc" / "d.jpg")
    (mine / "2021").mkdir()
    shutil.move(str(mine / "2020" / "sub" / "b.jpg"), str(mine / "2021" / "b_renamed.jpg"))
    (mine / "misc" / "c.jpg").unlink()
    call("POST", "/api/ref/scan")
    j = wait("refscan")
    ph = call("GET", "/api/photos?limit=99")[1]
    names = sorted(p["filename"] for p in ph)
    check("the list follows the folder: new file added, moved file renamed, deleted file gone",
          names == ["a.jpg", "b_renamed.jpg", "d.jpg"] and (j["result"]["added"], j["result"]["moved"], j["result"]["removed"]) == (1, 1, 1), (names, j["result"]))
    moved = call("GET", f"/api/photo/{pid_b}")[1]
    check("a moved photo keeps its rating (same photo, new place)", moved["rating"] == 5 and moved["filename"] == "b_renamed.jpg")

    # ---- an unplugged drive / missing folder never empties the catalog
    n = len(ph)
    os.rename(mine, tmp / "My Pictures (away)")
    call("POST", "/api/ref/scan")
    j = wait("refscan")
    check("folder missing: the scan reports it and the catalog keeps everything", j["state"] == "error" and len(call("GET", "/api/photos?limit=99")[1]) == n, j.get("error"))
    os.rename(tmp / "My Pictures (away)", mine)
    call("POST", "/api/ref/scan")
    j = wait("refscan")
    check("folder back: nothing was lost or duplicated", j["state"] == "done" and len(call("GET", "/api/photos?limit=99")[1]) == n and j["result"]["added"] == 0, j["result"])

    # ---- photag does not change these files
    after_scan = snapshot(mine)
    code, r = call("POST", f"/api/photo/{pid_a}/rotate", {"degrees": 90})
    check("rotate is refused for a photo in your folder", code == 409, r)
    code, r = call("POST", f"/api/photo/{pid_a}/edit", {"exposure": 0.5})
    check("develop edits are refused", code == 409, code)
    code, r = call("PATCH", f"/api/photo/{pid_a}", {"description": "x", "write_exif": True})
    check("writing metadata into the file is refused", code == 409, code)
    code, r = call("PATCH", f"/api/photo/{pid_a}", {"description": "kept in the catalog only"})
    check("ratings, keywords and descriptions still work (they live in the catalog)", code == 200 and call("GET", f"/api/photo/{pid_a}")[1]["description"] == "kept in the catalog only")
    call("POST", f"/api/photo/{pid_a}/compress", {"options": {"quality": 50}})
    j = wait("compress")
    check("compressing is refused", j["state"] == "error", j.get("error"))
    check("still nothing in the folder changed", snapshot(mine) == after_scan)

    # ---- backups stay in the normal place and DO hold your folder's photos (under _external; they were left out before 12.1.0)
    code, r = call("POST", "/api/backup/run", {})
    j = wait("backup")
    snaps = call("GET", "/api/backup")[1]["snapshots"]
    mdir = tmp / "bk" / snaps[0]["media_dir"] if snaps and snaps[0].get("media_dir") else None
    check("a backup works and is stored in the normal backup folder", j["state"] == "done" and snaps, j.get("error"))
    check("your folder's photos are copied into the backup, under _external", mdir is not None and any((mdir / "_external").rglob("*.jpg")))
    check("...and not loose next to the library's own photos", mdir is not None and not any(p.parent == mdir for p in mdir.glob("*.jpg")))
    check("nothing in the folder changed by the backup", snapshot(mine) == after_scan)

    # ---- trash: moving to the trash leaves the file; deleting from the trash sends it to the Recycle Bin
    f_d = mine / "misc" / "d.jpg"
    pid_d = next(p["id"] for p in ph if p["filename"] == "d.jpg")
    call("PATCH", "/api/photos", {"ids": [pid_d], "trashed": 1})
    check("moving to the trash does not touch the file", f_d.exists() and len(call("GET", "/api/photos?trashed=1&limit=99")[1]) == 1)
    code, r = call("POST", "/api/photos/delete-forever", {"ids": [pid_d]})
    check("deleting from the trash removes the photo from the catalog", code == 200 and r["deleted"] == 1 and not call("GET", f"/api/photo/{pid_d}")[0] == 200)
    check("...and the file went to the Recycle Bin (it is no longer in your folder)", not f_d.exists())
    check("the other files are all still there", (mine / "2020" / "a.jpg").exists() and (mine / "2021" / "b_renamed.jpg").exists())
    call("POST", "/api/ref/scan")
    j = wait("refscan")
    check("a scan after that finds nothing odd", j["state"] == "done" and (j["result"]["added"], j["result"]["removed"]) == (0, 0), j["result"])

    # ---- turning it off keeps the photos, changes nothing
    call("POST", "/api/ref", {"enabled": False})
    check("turned off again: the photos stay in the catalog and in the folder",
          len(call("GET", "/api/photos?limit=99")[1]) == 2 and (mine / "2020" / "a.jpg").exists() and call("GET", "/api/ref")[1]["enabled"] is False)
finally:
    srv.terminate()
    try:
        srv.wait(timeout=15)
    except Exception:
        srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
