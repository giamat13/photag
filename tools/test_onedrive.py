"""Test: folders stored in OneDrive. Files that are only in the cloud ("Free up space") are never read -- no hashing, no preview,
no analysis -- so a scan cannot download a whole library; they are not forgotten either (a known photo whose file went online-only
stays in the catalog; a new one is added once it is on this computer). A file locked for a moment while OneDrive syncs it is retried.
The catalog being inside OneDrive is detected. Cloud-only is simulated with PHOTAG_TEST_ONLINE_ONLY (any file whose name contains it).

    py -3.12 tools/test_onedrive.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "tools" / "sandbox" / "photos"
PORT = 8785
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_onedrive_test_"))
for k, v in (("APPDATA", "appdata"), ("LOCALAPPDATA", "local"), ("USERPROFILE", "home")):
    (tmp / v).mkdir()
    os.environ[k] = str(tmp / v)
os.environ.update(PYTHONIOENCODING="utf-8", PHOTAG_NO_OPEN="1", PHOTAG_BACKUP_START_DELAY="9999", PHOTAG_AUTOIMPORT_SETTLE="0")
sys.path.insert(0, str(ROOT))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


from app import analysis, cloud, config, db, importer, refmode  # noqa: E402
from app.config import PATHS  # noqa: E402

# ---- detection ----------------------------------------------------------------------------------------------------
FAKE = lambda attrs: types.SimpleNamespace(st_mode=0o100644, st_file_attributes=attrs)
check("a placeholder (recall on data access) is online-only", cloud.is_online_only(FAKE(0x400000)))
check("so are 'recall on open' and 'offline' placeholders", cloud.is_online_only(FAKE(0x40000)) and cloud.is_online_only(FAKE(0x1000)))
check("a normal file, or one that is pinned on this computer, is not", not cloud.is_online_only(FAKE(0x20)) and not cloud.is_online_only(FAKE(0x20 | 0x80000)))
check("a file system without such attributes (or a missing file) is never online-only", not cloud.is_online_only(__file__) and not cloud.is_online_only(tmp / "nope.jpg"))
od = tmp / "home" / "OneDrive - Contoso"
(od / "Pictures").mkdir(parents=True)
check("a folder under a folder named like OneDrive is in OneDrive", cloud.in_onedrive(od / "Pictures") and cloud.in_onedrive(tmp / "x" / "OneDrive" / "a") and cloud.in_onedrive(tmp / "OneDrive (Personal)" / "a"))
check("an ordinary folder is not", not cloud.in_onedrive(tmp / "home" / "Pictures") and not cloud.in_onedrive(tmp / "OneDriveStuff" / "a"))
os.environ["PHOTAG_TEST_ONEDRIVE"] = str(tmp / "elsewhere")
check("the OneDrive environment variables count too", cloud.in_onedrive(tmp / "elsewhere" / "Photos") and not cloud.in_onedrive(tmp / "elsewhere2"))
del os.environ["PHOTAG_TEST_ONEDRIVE"]

# ---- replace with retries -----------------------------------------------------------------------------------------------
a, b = tmp / "a.txt", tmp / "b.txt"
a.write_text("x")
real, calls = os.replace, []


def flaky(s, d):
    calls.append(1)
    if len(calls) < 3:
        raise PermissionError(13, "The process cannot access the file because it is being used by another process")
    return real(s, d)


real_sleep = time.sleep
cloud.os.replace = flaky
time.sleep = lambda s: None                       # (cloud.time is this same module: put it back below)
cloud.replace(a, b)
check("a file locked for a moment (OneDrive syncing it) is replaced after a few tries", b.read_text() == "x" and len(calls) == 3, len(calls))
calls.clear()
a.write_text("y")
cloud.os.replace = lambda s, d: (_ for _ in ()).throw(PermissionError(13, "locked"))
try:
    cloud.replace(a, b); gave_up = False
except PermissionError:
    gave_up = True
cloud.os.replace = real
time.sleep = real_sleep
check("...but a file that stays locked is reported in the end, not hidden", gave_up)

# ---- photos stay in my folder: online-only files are not read ----------------------------------------------------------------
config.set_library_root(tmp / "lib")
PATHS.refresh()
con = db.init_db()
folder = tmp / "OneDrive" / "Pictures"
folder.mkdir(parents=True)
shutil.copy2(SAMPLES / "paris.jpg", folder / "paris.jpg")
shutil.copy2(SAMPLES / "jerusalem.jpg", folder / "jerusalem.jpg")
s1 = refmode.scan(con, str(folder))
check("a normal scan adds the photos", s1["added"] == 2 and s1["cloud"] == 0, s1)
os.environ["PHOTAG_TEST_ONLINE_ONLY"] = "_CLOUD"
shutil.copy2(SAMPLES / "telaviv_1.jpg", folder / "telaviv_CLOUD.jpg")
reads = []
real_sha = refmode.images.sha256_file
refmode.images.sha256_file = lambda p, *a: (reads.append(str(p)), real_sha(p, *a))[1]
s2 = refmode.scan(con, str(folder))
check("a new cloud-only file is neither read nor added, and is counted", s2["added"] == 0 and s2["cloud"] == 1 and not any("_CLOUD" in r for r in reads), (s2, reads))
check("...and the catalog still holds only the two photos", con.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 2)
os.environ["PHOTAG_TEST_ONLINE_ONLY"] = "_CLOUD,paris"                      # a known photo whose file became online-only ("Free up space")
os.utime(folder / "paris.jpg", (time.time(), time.time() + 5))       # OneDrive often touches the date as well
s3 = refmode.scan(con, str(folder))
check("a known photo that went cloud-only is NOT removed and not re-read", s3["removed"] == 0 and s3["cloud"] >= 1 and con.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 2, s3)
del os.environ["PHOTAG_TEST_ONLINE_ONLY"]
refmode.images.sha256_file = real_sha
s4 = refmode.scan(con, str(folder))
check("once the file is on this computer it is added by the next scan", s4["added"] == 1 and s4["cloud"] == 0, s4)

# ---- automatic import ---------------------------------------------------------------------------------------------------
watch = tmp / "OneDrive" / "Camera"
watch.mkdir(parents=True)
shutil.copy2(SAMPLES / "nogps.jpg", watch / "new_CLOUD.jpg")
shutil.copy2(SAMPLES / "telaviv_2.jpg", watch / "now.jpg")
os.environ["PHOTAG_TEST_ONLINE_ONLY"] = "_CLOUD"
old = time.time() - 3600
for f in watch.iterdir():
    os.utime(f, (old, old))
r = importer.run_auto_import(str(watch), importer.Progress())
check("auto-import takes the local file and leaves the cloud-only one alone", r["added"] == 1 and r["cloud"] == 1 and r["failed"] == 0, r)
r = importer.run_auto_import(str(watch), importer.Progress())
check("...and keeps waiting for it (it was not marked as seen or failed)", r["cloud"] == 1 and r["added"] == 0, r)
del os.environ["PHOTAG_TEST_ONLINE_ONLY"]
r = importer.run_auto_import(str(watch), importer.Progress())
check("when it arrives on this computer it is imported", r["added"] == 1 and r["cloud"] == 0, r)

# ---- import dialog list -------------------------------------------------------------------------------------------------
os.environ["PHOTAG_TEST_ONLINE_ONLY"] = "_CLOUD"
files = {f["name"]: f for f in importer.scan_folder(str(tmp / "OneDrive" / "Pictures"))}
check("the import list marks cloud-only files", files["telaviv_CLOUD.jpg"]["online"] is True and files["paris.jpg"]["online"] is False, {k: v["online"] for k, v in files.items()})

# ---- analysis leaves cloud-only photos pending --------------------------------------------------------------------------
con.execute("UPDATE photos SET filename='x_CLOUD.jpg' WHERE id=1")
rel = con.execute("SELECT rel_path FROM photos WHERE id=1").fetchone()[0]
shutil.move(str(PATHS.media / rel), str(PATHS.media / rel.replace(".jpg", "_CLOUD.jpg")))
con.execute("UPDATE photos SET rel_path=? WHERE id=1", (rel.replace(".jpg", "_CLOUD.jpg"),))
con.commit()
p = importer.Progress()
analysis.run_analysis(False, p)
n_done = con.execute("SELECT COUNT(*) FROM photo_analysis").fetchone()[0]
check("analysis skips a cloud-only photo (still pending, not failed) and says so", con.execute("SELECT 1 FROM photo_analysis WHERE photo_id=1").fetchone() is None and n_done >= 1 and "OneDrive" in p.msg, (n_done, p.msg))
con.close()

# ---- through the server: previews, the library inside OneDrive --------------------------------------------------------------
env = {**os.environ, "PHOTAG_TEST_ONEDRIVE": str(tmp / "OneDrive")}
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))


def call(m, path, b=None):
    r = urllib.request.Request(APP + path, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=60) as x:
        return x.status, x.read()


try:
    for _ in range(60):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    call("POST", "/api/settings/library", {"path": str(tmp / "lib")})
    st = json.loads(call("GET", "/api/status")[1])
    check("a library outside OneDrive raises no warning", st["library_in_onedrive"] is False)
    r = json.loads(call("POST", "/api/settings/library", {"path": str(tmp / "OneDrive" / "PhotagLib")})[1])
    st = json.loads(call("GET", "/api/status")[1])
    check("a library inside OneDrive is reported when it is chosen and in the status", r["library_in_onedrive"] and st["library_in_onedrive"], (r, st["library_in_onedrive"]))
    ok = call("GET", "/api/local-thumb?path=" + urllib.parse.quote(str(folder / "paris.jpg")))
    cl = call("GET", "/api/local-thumb?path=" + urllib.parse.quote(str(folder / "telaviv_CLOUD.jpg")))
    check("a preview is drawn for a local file but a cloud-only file is not downloaded for it", ok[0] == 200 and len(ok[1]) > 100 and cl[0] == 204, (ok[0], cl[0]))
    call("POST", "/api/backup/settings", {"folder": str(tmp / "bk")})
    bk = json.loads(call("GET", "/api/backup")[1])
    call("POST", "/api/backup/settings", {"folder": str(tmp / "OneDrive" / "Backups")})
    bk2 = json.loads(call("GET", "/api/backup")[1])
    check("a backup folder inside OneDrive is flagged so the dialog can say what it means", bk2["folder_in_onedrive"] is True and bk["folder_in_onedrive"] is False, (bk["folder_in_onedrive"], bk2["folder_in_onedrive"]))
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
