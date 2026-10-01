"""Test: every backup holds its own complete set of photo files (shared files are hard links, not copied again).

    py -3.12 tools/test_backup_media_sets.py
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_media_sets_"))
for k, v in (("APPDATA", "appdata"), ("LOCALAPPDATA", "local"), ("USERPROFILE", "home")):
    (tmp / v).mkdir()
    os.environ[k] = str(tmp / v)
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT))

from app import backup, config, db  # noqa: E402
from app.config import PATHS  # noqa: E402

res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


try:
    config.set_library_root(tmp / "lib")
    PATHS.refresh()
    db.init_db().close()
    backup.set_settings({"folder": str(tmp / "bk"), "include_media": True, "keep": 10})
    (PATHS.media / "2024").mkdir(parents=True, exist_ok=True)
    for i in range(5):
        (PATHS.media / "2024" / f"p{i}.jpg").write_bytes(os.urandom(50_000) + bytes([i]))
    bk = tmp / "bk"

    s1 = backup.create_snapshot("manual")
    s2 = backup.create_snapshot("manual")
    check("first backup copied all 5 files", s1["media"]["copied"] == 5 and s1["media"]["files"] == 5, s1["media"])
    check("second backup lists all 5 files too, none copied again (linked)", s2["media"]["files"] == 5 and s2["media"]["copied"] == 0 and s2["media"]["linked"] == 5, s2["media"])
    check("each backup has its own media folder with all files",
          all(len(list((bk / m["media_dir"]).rglob("*.jpg"))) == 5 for m in (s1, s2)) and s1["media_dir"] != s2["media_dir"])
    sz = {m["name"]: m["total_bytes"] for m in backup.list_snapshots()}
    check("each backup is listed with its FULL size (catalog + all photo files), not just the catalog",
          all(v > 250_000 for v in sz.values()) and len(sz) == 2, sz)
    check("shared files take the space once", backup.media_bytes() < 5 * 50_001 * 1.2, backup.media_bytes())

    # a photo is deleted and one is changed after the second backup
    (PATHS.media / "2024" / "p0.jpg").unlink()
    (PATHS.media / "2024" / "p1.jpg").write_bytes(os.urandom(60_000))
    s3 = backup.create_snapshot("manual")
    check("third backup: 4 files, only the changed one is copied", s3["media"]["files"] == 4 and s3["media"]["copied"] == 1, s3["media"])
    check("the deleted photo is still in the earlier backups", (bk / s2["media_dir"] / "2024" / "p0.jpg").is_file())
    check("the earlier backup keeps the OLD version of the changed photo",
          (bk / s2["media_dir"] / "2024" / "p1.jpg").stat().st_size == 50_001 and (bk / s3["media_dir"] / "2024" / "p1.jpg").stat().st_size == 60_000)

    snaps = {m["name"]: m for m in backup.list_snapshots()}
    check("every backup says its photo files can be restored", all(m["media_ok"] for m in snaps.values()))

    # restore from the second backup: the deleted photo comes back, the changed one is not overwritten
    con = db.connect(); con.close()
    r = backup.restore_snapshot(s2["name"], restore_media=True)
    check("restore brings back the photo deleted later", (PATHS.media / "2024" / "p0.jpg").is_file() and r["media_copied"] == 1, r["media_copied"])
    check("restore never overwrites a file that exists", (PATHS.media / "2024" / "p1.jpg").stat().st_size == 60_000)

    # deleting a backup removes its folder but not files other backups share
    backup.delete_snapshot(s1["name"])
    check("deleting a backup removes its media folder", not (bk / s1["media_dir"]).exists())
    check("files shared with later backups are still there", (bk / s2["media_dir"] / "2024" / "p3.jpg").is_file() and
          (bk / s2["media_dir"] / "2024" / "p3.jpg").stat().st_size == 50_001)

    # old layout: a shared media-mirror from an earlier version
    old = tmp / "bk2"; backup.set_settings({"folder": str(old)})
    (old / "media-mirror" / "2024").mkdir(parents=True)
    for p in (PATHS.media / "2024").glob("*.jpg"):
        shutil.copy2(p, old / "media-mirror" / "2024" / p.name)
    s4 = backup.create_snapshot("manual")
    check("the first backup after an upgrade links the old mirror's files (no copying)", s4["media"]["copied"] == 0 and s4["media"]["linked"] == 5, s4["media"])
    check("the old mirror is deleted once no backup needs it", not (old / "media-mirror").exists())
    check("and the backup still has all its files", len(list((old / s4["media_dir"]).rglob("*.jpg"))) == 5)

    # a backup that died (installer closed it, crash) leaves a lock and a half-written photo folder behind
    import subprocess, time
    dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()
    d = backup.backup_dir()
    (d / ".backup.lock").write_text(f"{dead.pid} {time.time()}")
    (d / "media-photag-19990101-000000-auto.part").mkdir()
    ((d / "media-photag-19990101-000000-auto.part") / "x.jpg").write_bytes(b"x")
    s5 = backup.create_snapshot("manual")
    check("a lock left by a dead backup does not block the next one", s5["name"].endswith(".zip"))
    check("the half-written photo folder of the dead backup is cleaned up", not (d / "media-photag-19990101-000000-auto.part").exists())
    (d / ".backup.lock").write_text(f"{os.getpid()} {time.time()}")                  # this very process is alive
    try:
        backup.create_snapshot("manual"); busy = False
    except backup.BusyError:
        busy = True
    check("a lock held by a live backup still blocks (no two backups at once)", busy)
    (d / ".backup.lock").unlink(missing_ok=True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
