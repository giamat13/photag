"""Test: the full EXIF of every photo is kept in the catalog (photos.exif_json).

  * images.exif_full / exif_json_text: every tag, readable names, JSON-safe values, never raises
  * every import path fills it in (folder import, ZIP/social import, Takeout)
  * exifindex.get (on demand) and backfill_batch (cursor, limit, idempotent, never overwrites, skips missing files)
  * a legacy edited photo reads EXIF from its pristine original, not the re-saved working file
  * the real server: GET /api/photo/{id}/exif, and the background loop fills photos that predate the column

    py -3.12 tools/test_exif_full.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from PIL import Image
from PIL.TiffImagePlugin import IFDRational

ROOT = Path(__file__).resolve().parent.parent
PORT = 8791
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_exif_full_test_"))
for k, v in (("APPDATA", "a"), ("LOCALAPPDATA", "l"), ("USERPROFILE", "h"), ("HOME", "h")):
    (tmp / v).mkdir(exist_ok=True)
    os.environ[k] = str(tmp / v)
os.environ.update(PYTHONIOENCODING="utf-8", PHOTAG_NO_OPEN="1", PHOTAG_BACKUP_START_DELAY="9999",
                  PHOTAG_EXIF_DELAY="1", PHOTAG_EXIF_TICK="0.2", PHOTAG_EXIF_IDLE="1")
sys.path.insert(0, str(ROOT))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


from app import config, db, exifindex, images, importer  # noqa: E402
from app.config import PATHS  # noqa: E402


def rich_jpeg(path: Path, make="Canon", color=(10, 20, 30)):
    exif = Image.Exif()
    exif[271] = make
    exif[272] = "EOS R5"
    exif[274] = 6
    sub = exif.get_ifd(0x8769)
    sub[36867] = "2024:06:01 10:30:00"
    sub[33437] = 2.8
    sub[34855] = 400
    sub[37386] = 35.0
    sub[42036] = "RF35mm F1.8"
    sub[37500] = b"\x01" * 500
    gps = exif.get_ifd(0x8825)
    gps[1], gps[2], gps[3], gps[4] = "N", (32.0, 3.0, 0.0), "E", (34.0, 46.0, 48.0)
    Image.new("RGB", (40, 30), color).save(path, "JPEG", exif=exif)
    return path


def plain_jpeg(path: Path, color=(1, 2, 3)):
    Image.new("RGB", (40, 30), color).save(path, "JPEG")
    return path


work = tmp / "work"
work.mkdir()

# ---------------------------------------------------------------- exif_full / values
rich = rich_jpeg(work / "rich.jpg")
ex = images.exif_full(rich)
check("the Image group has Make/Model/Orientation by name", ex.get("Image", {}).get("Make") == "Canon" and ex["Image"].get("Orientation") == 6, ex.get("Image"))
check("the Exif group has the shooting data by name", ex.get("Exif", {}).get("DateTimeOriginal") == "2024:06:01 10:30:00" and ex["Exif"].get("LensModel") == "RF35mm F1.8", ex.get("Exif"))
check("rationals become plain numbers", ex["Exif"].get("FNumber") == 2.8 and ex["Exif"].get("FocalLength") == 35, ex["Exif"])
check("GPS is its own group, coordinates as lists", ex.get("GPS", {}).get("GPSLatitude") == [32, 3, 0] and ex["GPS"].get("GPSLongitudeRef") == "E", ex.get("GPS"))
check("a big binary blob (MakerNote) is noted by size, not stored", ex["Exif"].get("MakerNote") == "<500 bytes>", ex["Exif"].get("MakerNote"))
check("the pointers to the other groups are not listed as tags", not any(k in ex["Image"] for k in ("ExifOffset", "GPSInfo")), list(ex["Image"]))
check("the whole thing is JSON-serializable", json.loads(json.dumps(ex)) == ex)

check("a JPEG without EXIF gives {}", images.exif_full(plain_jpeg(work / "plain.jpg")) == {})
junk = work / "junk.jpg"
junk.write_bytes(b"this is not an image" * 50)
check("a file that is not an image gives {} and does not raise", images.exif_full(junk) == {})
check("a missing file gives {} and does not raise", images.exif_full(work / "nope.jpg") == {})
trunc = work / "trunc.jpg"
trunc.write_bytes(rich.read_bytes()[:60])
check("a truncated file does not raise", isinstance(images.exif_full(trunc), dict))
vid = work / "clip.mp4"
vid.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100)
check("a video gives {} without being opened", images.exif_full(vid) == {})

v = images._exif_value
check("small printable bytes become text", v(b"ABC\x00") == "ABC")
check("small non-printable bytes become hex", v(b"\x01\x02\xff") == "0102ff")
check("1/0 (a damaged rational) is a string, never NaN/Infinity", isinstance(v(IFDRational(1, 0)), str) and "NaN" not in json.dumps(v(IFDRational(1, 0))), v(IFDRational(1, 0)))
check("0/0 too", isinstance(v(IFDRational(0, 0)), str))
check("a long list is capped", len(v(list(range(1000)))) == 64)
check("deep nesting does not recurse forever", isinstance(v([[[[[[1]]]]]]), list))
check("an arbitrary object is stringified", v(object()).startswith("<object"))
check("a whole number float is an int", v(IFDRational(70, 2)) == 35 and isinstance(v(IFDRational(70, 2)), int))

_real = images.exif_full
images.exif_full = lambda p: {"Image": {"Artist": "שלום עולם"}}
text = images.exif_json_text(work / "x")
images.exif_full = _real
check("Hebrew is stored as readable text, not \\u escapes", "שלום עולם" in text and "\\u" not in text, text)

# ---------------------------------------------------------------- import paths
config.PATHS.root.mkdir(parents=True, exist_ok=True)
con = db.init_db()
pid_rich, new = importer._ingest_file(con, rich)
con.commit()
row = con.execute("SELECT exif_json FROM photos WHERE id=?", (pid_rich,)).fetchone()
check("folder import (_ingest_file) stores the full EXIF", new and json.loads(row["exif_json"]).get("Image", {}).get("Make") == "Canon", row["exif_json"][:80])
pid_plain, _ = importer._ingest_file(con, work / "plain.jpg")
con.commit()
check("a photo with no EXIF is stored as '{}' (looked, nothing there) -- not NULL",
      con.execute("SELECT exif_json FROM photos WHERE id=?", (pid_plain,)).fetchone()["exif_json"] == "{}")

zpath = tmp / "export.zip"
with zipfile.ZipFile(zpath, "w") as zf:
    zf.writestr("media/a.jpg", rich_jpeg(work / "z1.jpg", color=(50, 50, 50)).read_bytes())
class _P:
    done = total = 0; cancel = False; state = None; error = None; extra = {}
    def say(self, *a, **k): pass
    def say_parts(self, *a, **k): pass
    def fail(self, msg, **v): self.error = msg.format(**v)
importer.run_social_import(str(zpath), _P())
r = con.execute("SELECT exif_json FROM photos WHERE filename='a.jpg'").fetchone()
check("ZIP / social import stores the full EXIF", r and json.loads(r["exif_json"]).get("Exif", {}).get("LensModel") == "RF35mm F1.8", r and r["exif_json"][:80])

con.execute("INSERT OR IGNORE INTO albums(name,kind) VALUES('Trip','album')")
aid = con.execute("SELECT id FROM albums WHERE name='Trip'").fetchone()["id"]
tpath = tmp / "takeout.zip"
with zipfile.ZipFile(tpath, "w") as zf:            # different pixels than the social import's photo, so it is not a duplicate
    zf.writestr("Takeout/Google Photos/Trip/t.jpg", rich_jpeg(work / "t1.jpg", make="Sony", color=(99, 98, 97)).read_bytes())
with zipfile.ZipFile(tpath) as zf:
    status, _ = importer._ingest_media(con, zf, "Takeout/Google Photos/Trip/t.jpg", "takeout_t.jpg", aid, {})
con.commit()
r = con.execute("SELECT exif_json FROM photos WHERE filename='takeout_t.jpg'").fetchone()
check("Takeout import (_ingest_media) stores the full EXIF too",
      status == "added" and r and json.loads(r["exif_json"]).get("Image", {}).get("Make") == "Sony", (status, r and r["exif_json"][:60]))

# ---------------------------------------------------------------- exifindex.get on demand
(PATHS.media / "2024").mkdir(parents=True, exist_ok=True)


def seed(name, src: Path | None, exif_json=None, orig_backup=None, sha=None):
    dest = PATHS.media / "2024" / name
    if src is not None:
        dest.write_bytes(src.read_bytes())
    return con.execute("INSERT INTO photos(sha256,filename,rel_path,bytes,exif_json,orig_backup) VALUES(?,?,?,?,?,?)",
                       (sha or f"sha-{name}", name, f"2024/{name}", 1, exif_json, orig_backup)).lastrowid


a = seed("od.jpg", rich_jpeg(work / "od_src.jpg", color=(7, 7, 7)))
con.commit()
g = exifindex.get(con, a)
check("get() reads a NULL photo from its file", g and g["Image"]["Make"] == "Canon", g and list(g))
check("...and stores it", json.loads(con.execute("SELECT exif_json FROM photos WHERE id=?", (a,)).fetchone()["exif_json"]) == g)
(PATHS.media / "2024" / "od.jpg").unlink()
check("the second get() answers from the catalog -- the file is not needed any more", exifindex.get(con, a) == g)
check("get() of an unknown photo is None", exifindex.get(con, 999999) is None)
b = seed("missing.jpg", None)
con.commit()
check("get() of a photo whose file is missing is None (and nothing is stored)",
      exifindex.get(con, b) is None and con.execute("SELECT exif_json FROM photos WHERE id=?", (b,)).fetchone()["exif_json"] is None)
c = seed("damaged.jpg", rich_jpeg(work / "dm_src.jpg", color=(8, 8, 8)), exif_json="{not json")
con.commit()
gc = exifindex.get(con, c)
check("get() re-reads a photo whose stored text is damaged", gc and gc["Image"]["Make"] == "Canon", gc and list(gc))

# legacy edited photo: the working file was re-saved (EXIF gone), the pristine original is orig_backup
(PATHS.media / ".originals").mkdir(exist_ok=True)
rich_jpeg(PATHS.media / ".originals" / "9_leg.jpg", make="Nikon", color=(9, 9, 9))
d = seed("leg.jpg", plain_jpeg(work / "leg_src.jpg"), orig_backup=".originals/9_leg.jpg")
con.commit()
gd = exifindex.get(con, d)
check("a legacy edited photo reads its EXIF from orig_backup, not the re-saved working file", gd and gd["Image"]["Make"] == "Nikon", gd)

# ---------------------------------------------------------------- backfill_batch
con.execute("DELETE FROM photos")
con.commit()
ids = [seed(f"bf{i}.jpg", rich_jpeg(work / f"bf{i}.jpg", color=(i, i, i))) for i in range(7)]
miss = seed("bf_missing.jpg", None)
done_id = seed("bf_done.jpg", plain_jpeg(work / "bf_done_src.jpg"), exif_json='{"sentinel":1}')
con.commit()

d1, last1 = exifindex.backfill_batch(con, 0, 3)
check("a batch honours the limit", d1 == 3, d1)
check("the cursor moves to the last row looked at", last1 == ids[2], last1)
filled = lambda: [r["id"] for r in con.execute("SELECT id FROM photos WHERE exif_json IS NOT NULL ORDER BY id")]
check("only the first three (plus the pre-filled one) are filled so far", filled() == ids[:3] + [done_id], filled())
d2, last2 = exifindex.backfill_batch(con, last1, 100)
check("the next batch continues after the cursor and finishes the rest (the missing file is skipped)", d2 == 4, d2)
check("a photo that already had EXIF is never overwritten",
      con.execute("SELECT exif_json FROM photos WHERE id=?", (done_id,)).fetchone()["exif_json"] == '{"sentinel":1}')
check("the missing file stays NULL so a later pass can retry it",
      con.execute("SELECT exif_json FROM photos WHERE id=?", (miss,)).fetchone()["exif_json"] is None)
d3, last3 = exifindex.backfill_batch(con, last2, 100)
check("when nothing is left the cursor stays and nothing is done", (d3, last3) == (0, last2), (d3, last3))
d4, _ = exifindex.backfill_batch(con, 0, 100)
check("a second full pass is idempotent (only the missing one is looked at, and it is still missing)", d4 == 0, d4)
(PATHS.media / "2024" / "bf_missing.jpg").write_bytes(rich_jpeg(work / "late.jpg").read_bytes())
d5, _ = exifindex.backfill_batch(con, 0, 100)
check("a file that shows up later is picked up by the next pass", d5 == 1, d5)

# ---------------------------------------------------------------- the real server
con.execute("DELETE FROM photos")
con.commit()
s1 = seed("srv1.jpg", rich_jpeg(work / "s1.jpg", color=(21, 21, 21)))
loop_ids = [seed(f"loop{i}.jpg", rich_jpeg(work / f"l{i}.jpg", color=(30 + i, 30 + i, 30 + i))) for i in range(3)]
con.commit()
con.close()

srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)],
                       env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(80):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2).read()
            break
        except Exception:
            time.sleep(0.5)
    else:
        raise SystemExit("server did not start")

    # the endpoint fills s1 on demand -- but the background loop may get there first; either way the answer is the full EXIF
    body = json.loads(urllib.request.urlopen(f"{APP}/api/photo/{s1}/exif", timeout=30).read())
    check("GET /api/photo/{id}/exif returns the full EXIF", body["exif"]["Image"]["Make"] == "Canon" and body["exif"]["GPS"]["GPSLatitudeRef"] == "N", list(body["exif"]))
    try:
        urllib.request.urlopen(f"{APP}/api/photo/999999/exif", timeout=30)
        check("an unknown photo is a 404", False)
    except urllib.error.HTTPError as e:
        check("an unknown photo is a 404", e.code == 404, e.code)

    deadline, left = time.time() + 40, None
    while time.time() < deadline:
        c2 = db.connect()
        left = c2.execute(f"SELECT COUNT(*) n FROM photos WHERE exif_json IS NULL AND id IN ({','.join(map(str, loop_ids))})").fetchone()["n"]
        c2.close()
        if left == 0:
            break
        time.sleep(0.5)
    check("the background loop fills photos that predate the column, by itself", left == 0, f"{left} still NULL")
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
