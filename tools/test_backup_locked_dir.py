"""Test: a backup still succeeds when Windows refuses to rename the finished photo folder ("[WinError 5] Access is denied" on
media-...-manual.part, because an antivirus / indexer holds a file in it open).

    py -3.12 tools/test_backup_locked_dir.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_locked_dir_"))
for k, v in (("APPDATA", "appdata"), ("LOCALAPPDATA", "local"), ("USERPROFILE", "home")):
    (tmp / v).mkdir()
    os.environ[k] = str(tmp / v)
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT))

from app import backup, cloud, config, db  # noqa: E402
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
    for i in range(3):
        (PATHS.media / "2024" / f"p{i}.jpg").write_bytes(os.urandom(20_000) + bytes([i]))
    real = cloud.replace

    def locked(src, dst, tries=12):                       # a folder cannot be renamed; files can
        if Path(src).is_dir():
            raise PermissionError(5, "Access is denied", str(src))
        return real(src, dst, tries)
    cloud.replace = locked
    s = backup.create_snapshot("manual")
    cloud.replace = real
    d = tmp / "bk"
    mdir = d / s["media_dir"]
    check("backup finished although the folder could not be renamed", (d / s["name"]).is_file())
    check("the photo set is complete under its final name", sorted(p.name for p in (mdir / "2024").glob("*.jpg")) == ["p0.jpg", "p1.jpg", "p2.jpg"])
    check("the contents are the real files", (mdir / "2024" / "p1.jpg").read_bytes() == (PATHS.media / "2024" / "p1.jpg").read_bytes())
    check("no .part folder is left", not list(d.glob("media-*.part")))
    check("the backup can be listed", any(m["name"] == s["name"] for m in backup.list_snapshots()))
finally:
    try:
        cloud.replace = real
    except NameError:
        pass
print(f"{sum(res)}/{len(res)} passed")
sys.exit(0 if res and all(res) else 1)
