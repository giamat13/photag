"""Test: importer.find_lrcat_candidates() -- scans Lightroom's usual default folders for
.lrcat catalog files, so the import screen can offer them instead of making the user browse.

    py -3.12 tools/test_lrcat_candidates.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_lrcat_candidates_test_"))
for k, v in (("APPDATA", "a"), ("LOCALAPPDATA", "l"), ("USERPROFILE", "h"), ("HOME", "h")):
    (tmp / v).mkdir(exist_ok=True)
    os.environ[k] = str(tmp / v)
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


from app import importer  # noqa: E402

home = tmp / "h"

check("no candidates when nothing exists", importer.find_lrcat_candidates() == [])

pics_lr = home / "Pictures" / "Lightroom"
pics_lr.mkdir(parents=True)
older = pics_lr / "Old Catalog.lrcat"
older.write_bytes(b"old")
newer = pics_lr / "New Catalog.lrcat"
time.sleep(0.05)
newer.write_bytes(b"newer")
not_a_catalog = pics_lr / "notes.txt"
not_a_catalog.write_text("hi")

out = importer.find_lrcat_candidates()
check("found both catalogs in the default folder", len(out) == 2, out)
check("ignores non-.lrcat files", not any(c["name"] == "notes.txt" for c in out))
check("newest catalog sorts first", out and out[0]["name"] == "New Catalog.lrcat", out)
check("each entry has path/name/bytes/mtime", out and all(k in out[0] for k in ("path", "name", "bytes", "mtime")))

docs_lr = home / "Documents" / "Lightroom"
docs_lr.mkdir(parents=True)
(docs_lr / "Other.lrcat").write_bytes(b"x")
out2 = importer.find_lrcat_candidates()
check("also scans the Documents/Lightroom default folder", len(out2) == 3, out2)

many_root = home / "Pictures"
for i in range(15):
    (many_root / f"direct{i}.lrcat").write_bytes(b"x")
out3 = importer.find_lrcat_candidates(limit=5)
check("respects the limit parameter", len(out3) == 5, len(out3))

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
