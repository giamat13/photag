"""Test: backups with reduced copies of the photos (strong compression, capped to HD) for big libraries.
Originals in the library are never touched; videos / RAW are copied as they are; unchanged photos are re-used by later
backups (not encoded again); safety snapshots always hold the originals; restoring never replaces an existing file.

    py -3.12 tools/test_backup_reduced.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "tools" / "sandbox" / "photos"
tmp = Path(tempfile.mkdtemp(prefix="photag_reduced_"))
for k, v in (("APPDATA", "appdata"), ("LOCALAPPDATA", "local"), ("USERPROFILE", "home")):
    (tmp / v).mkdir()
    os.environ[k] = str(tmp / v)
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT))

from app import backup, config, db  # noqa: E402
from app.config import PATHS  # noqa: E402

res = []


def _open(p):
    """(size, format, has exif), with the file closed again -- on Windows an open image would block deleting its folder."""
    with Image.open(p) as im:
        return im.size, im.format, bool(im.info.get("exif"))


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


config.set_library_root(tmp / "lib")
PATHS.refresh()
db.init_db().close()
bk = tmp / "bk"
m = PATHS.media / "2024"
m.mkdir(parents=True, exist_ok=True)
shutil.copy2(SAMPLES / "big_q97_exif.jpg", m / "big.jpg")                  # 3000 x 2000, EXIF
Image.open(SAMPLES / "paris.jpg").convert("RGB").resize((2400, 1600)).save(m / "paris.png")
shutil.copy2(SAMPLES / "telaviv_1.jpg", m / "small.jpg")                     # 900 x 600: already below HD
(m / "clip.mp4").write_bytes(os.urandom(40_000))
(m / "raw.cr2").write_bytes(os.urandom(30_000))
(m / "broken.jpg").write_bytes(b"not really a jpeg" * 100)
orig = {p.name: p.read_bytes() for p in m.iterdir()}

d = backup.get_settings()
check("defaults: reduced copies off (existing users keep full backups), strong compression and HD ready", d["compress_media"] is False and d["compress_quality"] == 60 and d["compress_max_side"] == 1280 and d["include_videos"], d)
backup.set_settings({"folder": str(bk), "include_media": True, "keep": 10, "compress_media": True})
s = backup.get_settings()
check("turning it on keeps strong compression + HD as the defaults", s["compress_media"] and s["compress_quality"] == 60 and s["compress_max_side"] == 1280, s)
check("nonsense values are normalised", backup.set_settings({"compress_quality": 5, "compress_max_side": 17})["compress_quality"] == 40 and backup.get_settings()["compress_max_side"] == 0)
backup.set_settings({"compress_quality": 60, "compress_max_side": 1280})

s1 = backup.create_snapshot("manual")
d1 = bk / s1["media_dir"] / "2024"
check("the manifest says the set is reduced", s1["media_compressed"] and s1["media"]["reduced"] and s1["media"]["quality"] == 60 and s1["media"]["max_side"] == 1280, s1["media"])
big_size = _open(d1 / "big.jpg")[0]
check("a big photo is capped to HD (long side 1280)", max(big_size) == 1280 and big_size == (1280, 853), big_size)
check("...and is much smaller than the original", (d1 / "big.jpg").stat().st_size < len(orig["big.jpg"]) * 0.25, ((d1 / "big.jpg").stat().st_size, len(orig["big.jpg"])))
check("EXIF is carried over", _open(d1 / "big.jpg")[2])
check("a PNG is reduced too (same name, still a PNG)", _open(d1 / "paris.png")[1] == "PNG" and max(_open(d1 / "paris.png")[0]) == 1280)
check("a photo already below HD that gets no smaller is kept as it is", (d1 / "small.jpg").read_bytes() == orig["small.jpg"] or (d1 / "small.jpg").stat().st_size < len(orig["small.jpg"]))
check("a video and a RAW file are copied as they are", (d1 / "clip.mp4").read_bytes() == orig["clip.mp4"] and (d1 / "raw.cr2").read_bytes() == orig["raw.cr2"])
check("a file that cannot be re-encoded is copied, not lost", (d1 / "broken.jpg").read_bytes() == orig["broken.jpg"])
check("the library itself is untouched", all((m / n).read_bytes() == b for n, b in orig.items()))
check("every file of the library is in the backup (+ no stray files counted)", s1["media"]["files"] == 6 and len(backup._media_files(bk / s1["media_dir"])) == 6, s1["media"])
check("the backup's listed size is the size of the reduced set, far below the originals",
      s1["media"]["total_bytes"] < s1["media"]["source_bytes"] * 0.6, (s1["media"]["total_bytes"], s1["media"]["source_bytes"]))

s2 = backup.create_snapshot("manual")
check("the next backup re-uses what did not change (no encoding again)", s2["media"]["encoded"] == 0 and s2["media"]["linked"] == 6, s2["media"])
shutil.copy2(SAMPLES / "jerusalem.jpg", m / "new.jpg")
s3 = backup.create_snapshot("manual")
check("only a new photo is encoded", s3["media"]["encoded"] == 1 and s3["media"]["copied"] == 1 and s3["media"]["linked"] == 6, s3["media"])
backup.set_settings({"compress_max_side": 1920})
s4 = backup.create_snapshot("manual")
check("changing the size re-encodes the photos (the settings are part of 'up to date')", s4["media"]["encoded"] >= 2 and max(_open(bk / s4["media_dir"] / "2024" / "big.jpg")[0]) == 1920, s4["media"])
backup.set_settings({"compress_max_side": 0, "compress_quality": 60})
s5 = backup.create_snapshot("manual")
check("size 'keep' only lowers the quality", _open(bk / s5["media_dir"] / "2024" / "big.jpg")[0] == (3000, 2000))
backup.set_settings({"compress_max_side": 1280, "include_videos": False})
s6 = backup.create_snapshot("manual")
check("videos can be left out", not (bk / s6["media_dir"] / "2024" / "clip.mp4").exists() and (bk / s6["media_dir"] / "2024" / "big.jpg").exists() and s6["media"]["files"] == 6, s6["media"])
backup.set_settings({"include_videos": True})

# safety snapshots hold the real files; a normal backup after an originals backup does not link to reduced files
bc = backup.create_snapshot("before-compress")
check("a safety backup (before compress) holds the ORIGINALS even when reduced copies are on", not bc["media_compressed"] and
      (bk / bc["media_dir"] / "2024" / "big.jpg").read_bytes() == orig["big.jpg"], bc["media"])
backup.set_settings({"compress_media": False})
s7 = backup.create_snapshot("manual")
check("with reduced copies off the backup holds the originals again", not s7["media_compressed"] and (bk / s7["media_dir"] / "2024" / "big.jpg").read_bytes() == orig["big.jpg"])
check("...and never reuses a reduced file as if it were the original (it links the earlier originals set)", s7["media"]["linked"] == 7 and
      all((bk / s7["media_dir"] / "2024" / n).read_bytes() == b for n, b in orig.items()), s7["media"])

# restore from a reduced backup: gaps are filled, existing files are never replaced
(m / "big.jpg").unlink()
(m / "paris.png").write_bytes(b"changed on purpose")
r = backup.restore_snapshot(s1["name"], restore_media=True, overwrite_changed=True)
check("restoring from a reduced backup refills the missing photo", (m / "big.jpg").is_file() and max(_open(m / "big.jpg")[0]) == 1280, r)
check("...but never replaces a file that exists, even when asked to", (m / "paris.png").read_bytes() == b"changed on purpose")
snaps = {x["name"]: x for x in backup.list_snapshots()}
check("the listing tells which backups hold reduced copies", snaps[s1["name"]]["media_compressed"] and not snaps[s7["name"]]["media_compressed"])
check("the backup verifies (file count ignores the index)", not backup.verify_snapshot(snaps[s1["name"]]), backup.verify_snapshot(snaps[s1["name"]]))
backup.delete_snapshot(s1["name"])
check("deleting a reduced backup removes its folder", not (bk / s1["media_dir"]).exists())

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
