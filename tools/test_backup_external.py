"""Test: a backup also holds the photos that live OUTSIDE the library ("photos stay in my folder"): they used to be left out, so a
library of hundreds of GB made a backup of a few MB. They go into _external/<drive>/... of the photo set, a missing file or unplugged
drive is not an error, and a restore puts back only what is missing -- to the path the catalog has, never over an existing file.

    py -3.12 tools/test_backup_external.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_bkext_"))
for k, v in (("APPDATA", "appdata"), ("LOCALAPPDATA", "local"), ("USERPROFILE", "home")):
    (tmp / v).mkdir()
    os.environ[k] = str(tmp / v)
os.environ["HOME"] = str(tmp / "home")
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT))

from app import backup, config, db  # noqa: E402
from app.config import PATHS  # noqa: E402

res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


config.set_library_root(tmp / "lib")
PATHS.refresh()
db.init_db().close()
own = tmp / "MyPhotos"
(own / "trip").mkdir(parents=True)
Image.new("RGB", (3000, 2000), (10, 20, 30)).save(own / "a.jpg", "JPEG")
Image.new("RGB", (800, 600), (90, 20, 30)).save(own / "trip" / "b.jpg", "JPEG")
Image.new("RGB", (400, 300), (5, 200, 30)).save(own / "gone.jpg", "JPEG")           # will be missing at backup time
(PATHS.media / "2024").mkdir(parents=True, exist_ok=True)
Image.new("RGB", (500, 400), (1, 2, 3)).save(PATHS.media / "2024" / "inlib.jpg", "JPEG")
con = db.connect()
for i, (rel, n) in enumerate([("2024/inlib.jpg", 1), (str(own / "a.jpg"), 2), (str(own / "trip" / "b.jpg"), 3), (str(own / "gone.jpg"), 4)]):
    con.execute("INSERT INTO photos(sha256,filename,rel_path,bytes) VALUES(?,?,?,?)", (f"sha{n}", Path(rel).name, rel, 1))
con.commit()
con.close()
os.remove(own / "gone.jpg")
bk = tmp / "bk"
size_a = (own / "a.jpg").stat().st_size

for reduced in (True, False):
    backup.set_settings({"folder": str(bk), "include_media": True, "keep": 10, "compress_media": reduced})
    m = backup.create_snapshot("manual")
    md = bk / m["media_dir"]
    got = sorted(str(p.relative_to(md)).replace("\\", "/") for p in backup._media_files(md))
    ext = [g for g in got if g.startswith("_external/")]
    tag = "reduced" if reduced else "originals"
    check(f"[{tag}] the library photo and BOTH existing outside photos are in the set (3 files), the missing one is not an error", len(got) == 3 and len(ext) == 2 and any(g.endswith("trip/b.jpg") for g in ext), got)
    check(f"[{tag}] the manifest counts them", m["media"]["files"] == 3, m["media"])
    check(f"[{tag}] the backup checks out", backup.verify_snapshot(m) == [], backup.verify_snapshot(m))
    if reduced:
        a = next(md / g for g in ext if g.endswith("a.jpg"))
        check("[reduced] an outside photo is reduced like the others (smaller than the original)", a.stat().st_size < size_a)

check("the user's own files were not touched", (own / "a.jpg").stat().st_size == size_a and (own / "trip" / "b.jpg").is_file())

# ---- restore: the user lost a file (and a sub folder) -- it comes back where the catalog says; an existing file is never replaced
newest = backup.list_snapshots()[0]
os.remove(own / "trip" / "b.jpg")
shutil.rmtree(own / "trip")
(own / "a.jpg").write_bytes(b"changed on purpose")
r = backup.restore_snapshot(newest["name"], restore_media=True)
check("restore puts the lost outside photo back at its own path", (own / "trip" / "b.jpg").is_file() and Image.open(own / "trip" / "b.jpg").size[0] > 0)
check("restore never replaces an outside file that exists", (own / "a.jpg").read_bytes() == b"changed on purpose")
check("restore left nothing half-written", not list(own.rglob("*.part")))

shutil.rmtree(tmp, ignore_errors=True)
n = res.count(False)
print(f"\n{len(res) - n}/{len(res)} passed")
sys.exit(1 if n else 0)
