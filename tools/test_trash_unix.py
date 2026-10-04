"""Test: deleting for good sends a file to the macOS Trash / the freedesktop.org (Linux) trash instead of removing it outright."""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

tmp = Path(tempfile.mkdtemp(prefix="photag_trash_"))
os.environ["HOME"] = str(tmp / "home")
os.environ["XDG_DATA_HOME"] = str(tmp / "xdg")
(tmp / "home").mkdir()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import refmode  # noqa: E402

res = []
def check(n, ok, extra=""):
    res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n + (f"  [{extra}]" if extra else ""))

src = tmp / "my photos"; src.mkdir()
f = src / "a b.jpg"; f.write_bytes(b"one")
with mock.patch.object(sys, "platform", "linux"):
    check("linux: the file is sent to the trash (returns True, gone from the folder)", refmode._trash_unix(f) is True and not f.exists())
    tr = tmp / "xdg" / "Trash"
    moved = list((tr / "files").iterdir())
    check("...in Trash/files with its content", len(moved) == 1 and moved[0].read_bytes() == b"one", moved)
    info = (tr / "info" / (moved[0].name + ".trashinfo")).read_text()
    check("...with a .trashinfo that says where it came from (percent-encoded) and when", info.startswith("[Trash Info]") and "Path=" in info and "a%20b.jpg" in info and "DeletionDate=" in info, info)
    g = src / "a b.jpg"; g.write_bytes(b"two")
    refmode._trash_unix(g)
    names = sorted(x.name for x in (tr / "files").iterdir())
    check("a second file with the same name does not overwrite the first", len(names) == 2 and sorted((x.read_bytes() for x in (tr / "files").iterdir())) == [b"one", b"two"], names)
with mock.patch.object(sys, "platform", "darwin"):
    h = src / "m.jpg"; h.write_bytes(b"mac")
    check("macOS: the file goes to ~/.Trash", refmode._trash_unix(h) is True and not h.exists() and (tmp / "home" / ".Trash" / "m.jpg").read_bytes() == b"mac")
check("recycle(): a missing file counts as done", refmode.recycle(str(src / "nope.jpg")) is True)
with mock.patch.object(refmode.shutil if hasattr(refmode, "shutil") else __import__("shutil"), "move", side_effect=OSError("disk full")):
    k = src / "k.jpg"; k.write_bytes(b"k")
    with mock.patch.object(sys, "platform", "linux"):
        check("a failed move leaves the file alone and returns False", refmode._trash_unix(k) is False and k.exists())
n = res.count(False); print(f"\n{len(res)-n}/{len(res)} passed"); sys.exit(1 if n else 0)
