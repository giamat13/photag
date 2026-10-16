"""Test: the import dialog lists up to SCAN_CAP files and says how many there are in all when the folder has more.

    py -3.12 tools/test_scan_cap.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_scancap_test_"))
for k, v in (("APPDATA", "appdata"), ("LOCALAPPDATA", "local"), ("USERPROFILE", "home")):
    os.environ[k] = str(tmp / v)
    (tmp / v).mkdir()
sys.path.insert(0, str(ROOT))
from app import importer                                   # noqa: E402

res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


src = tmp / "src"
(src / "sub").mkdir(parents=True)
for i in range(8):
    (src / ("sub" if i % 2 else ".") / f"p{i}.jpg").write_bytes(b"x")
(src / "notes.txt").write_text("not media")
(src / ".hidden.jpg").write_bytes(b"x")

files, total = importer.scan_folder_total(str(src), True, cap=5)
check("the list stops at the cap", len(files) == 5, len(files))
check("the total counts past the cap", total == 8, total)
files, total = importer.scan_folder_total(str(src), True, cap=100)
check("under the cap everything is listed and total matches", len(files) == 8 and total == 8, (len(files), total))
check("scan_folder still returns a plain list", len(importer.scan_folder(str(src))) == 8)
check("the cap is 100,000", importer.SCAN_CAP == 100000, importer.SCAN_CAP)
check("not recursive only looks at the top folder", importer.scan_folder_total(str(src), False)[1] == 4)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
