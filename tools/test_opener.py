"""Test: app/opener.py runs the right command for each operating system."""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import opener  # noqa: E402

res = []
def check(n, ok, extra=""):
    res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n + (f"  [{extra}]" if extra else ""))

def calls(plat, fn, arg):
    with mock.patch.object(sys, "platform", plat), mock.patch.object(opener.subprocess, "Popen") as po:
        fn(arg)
        return po.call_args[0][0]

check("windows: explorer /select,", calls("win32", opener.reveal, "C:/a/b.jpg") == ["explorer", "/select,", "C:/a/b.jpg"])
check("macOS: open -R", calls("darwin", opener.reveal, "/a/b.jpg") == ["open", "-R", "/a/b.jpg"])
check("linux: xdg-open of the folder", calls("linux", opener.reveal, "/a/b.jpg") == ["xdg-open", "/a"])
check("macOS: open", calls("darwin", opener.open_default, "/a/b.mp4") == ["open", "/a/b.mp4"])
check("linux: xdg-open", calls("linux", opener.open_default, "/a/b.mp4") == ["xdg-open", "/a/b.mp4"])
with mock.patch.object(sys, "platform", "win32"), mock.patch.object(opener.os, "startfile", create=True) as sf:
    opener.open_default("C:/a/b.mp4")
    check("windows: os.startfile", sf.call_args[0][0] == "C:/a/b.mp4")
n = res.count(False); print(f"\n{len(res)-n}/{len(res)} passed"); sys.exit(1 if n else 0)
