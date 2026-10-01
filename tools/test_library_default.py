"""Tests for the default data folder (~/Photag) and the consented move of an old ~/PhotoManager library.
Every case runs in a fresh process with a throw-away profile (USERPROFILE / APPDATA), so nothing real is touched.

    py -3.12 tools/test_library_default.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def run(profile: Path, code: str) -> str:
    env = {**os.environ, "USERPROFILE": str(profile), "HOME": str(profile), "APPDATA": str(profile / "AppData"), "LOCALAPPDATA": str(profile / "Local"),
           "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, r'{ROOT}')\nfrom app import config\n{code}"], env=env, capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr[-400:]
    return r.stdout.strip()


def profile():
    p = Path(tempfile.mkdtemp(prefix="photag_lib_test_"))
    (p / "AppData").mkdir()
    return p


def legacy_library(p: Path):
    (p / "PhotoManager" / "media" / "2020").mkdir(parents=True)
    (p / "PhotoManager" / "media" / "2020" / "a.jpg").write_bytes(b"photo-bytes" * 100)
    (p / "PhotoManager" / "catalog.db").write_bytes(b"SQLite format 3\0" + b"x" * 500)
    (p / "PhotoManager" / "backups").mkdir()


def pointer(p: Path) -> dict:
    f = p / "AppData" / "photag" / "config.json"
    return json.loads(f.read_text("utf-8")) if f.exists() else {}


# ---- A: a brand-new profile uses ~/Photag
p = profile()
out = run(p, "print(config.get_library_root()); print(config.legacy_library_in_use())")
check("new install: the library goes to <home>\\Photag", out.splitlines()[0] == str(p / "Photag"), out.splitlines()[0])
check("new install: no 'move' offer", out.splitlines()[1] == "None")
shutil.rmtree(p, ignore_errors=True)

# ---- B: an old PhotoManager library keeps working, then moves when the user agrees
p = profile(); legacy_library(p)
out = run(p, "print(config.get_library_root()); print(config.legacy_library_in_use())")
check("old library: still used as is (nothing hidden or lost)", out.splitlines()[0] == str(p / "PhotoManager"), out.splitlines()[0])
check("old library: the move is offered", out.splitlines()[1] == str(p / "PhotoManager"))
run(p, "config.request_legacy_move()")
check("asking to move does not move anything yet", (p / "PhotoManager" / "catalog.db").exists() and not (p / "Photag").exists())
out = run(p, "print(config.get_library_root()); print(config.move_notice())")
root = out.splitlines()[0]
check("next start: the library is now <home>\\Photag", root == str(p / "Photag"), root)
check("catalog, photos and backups folder arrived intact", (p / "Photag" / "catalog.db").read_bytes().startswith(b"SQLite format 3") and (p / "Photag" / "media" / "2020" / "a.jpg").read_bytes() == b"photo-bytes" * 100 and (p / "Photag" / "backups").is_dir())
check("the old folder is gone (it was renamed, not copied)", not (p / "PhotoManager").exists())
check("the notice says where it came from", "moved_from" in out and "PhotoManager" in out.splitlines()[1], out.splitlines()[1])
out2 = run(p, "config.ack_move_notice(); print(config.move_notice()); print(config.get_library_root())")
check("after acknowledging, the notice is gone and the library stays in Photag", out2.splitlines()[0] == "{}" and out2.splitlines()[1] == str(p / "Photag"))
check("no move flag left in the settings", "move_legacy" not in pointer(p))
shutil.rmtree(p, ignore_errors=True)

# ---- C: both exist (the library is already in Photag): a stale request must not touch anything
p = profile(); legacy_library(p)
(p / "Photag").mkdir(); (p / "Photag" / "catalog.db").write_bytes(b"SQLite format 3\0new")
run(p, "config.request_legacy_move()")
out = run(p, "print(config.get_library_root())")
check("library already in Photag: it is used, the old folder is left alone", out == str(p / "Photag") and (p / "PhotoManager" / "catalog.db").exists())
shutil.rmtree(p, ignore_errors=True)

# ---- D: Photag exists and is not empty (but has no catalog): refuse to move, say why, keep using the old library
p = profile(); legacy_library(p)
(p / "Photag").mkdir(); (p / "Photag" / "something.txt").write_text("keep me")
run(p, "config.request_legacy_move()")
out = run(p, "print(config.get_library_root()); print(config.move_notice())")
check("a non-empty Photag folder blocks the move: nothing overwritten, the old library stays in use", out.splitlines()[0] == str(p / "PhotoManager") and (p / "Photag" / "something.txt").exists() and (p / "PhotoManager" / "catalog.db").exists(), out.splitlines()[0])
check("the reason is reported", "already exists" in out.splitlines()[1], out.splitlines()[1])
shutil.rmtree(p, ignore_errors=True)

# ---- E: an empty leftover Photag folder is fine
p = profile(); legacy_library(p)
(p / "Photag").mkdir()
run(p, "config.request_legacy_move()")
out = run(p, "print(config.get_library_root())")
check("an empty Photag folder is replaced by the moved library", out == str(p / "Photag") and (p / "Photag" / "catalog.db").exists() and not (p / "PhotoManager").exists())
shutil.rmtree(p, ignore_errors=True)

# ---- G: a background run (the backup task) never moves the folder under a running app
p = profile(); legacy_library(p)
run(p, "config.request_legacy_move()")
env_bg = {**os.environ, "USERPROFILE": str(p), "HOME": str(p), "APPDATA": str(p / "AppData"), "LOCALAPPDATA": str(p / "Local"), "PHOTAG_BACKGROUND": "1"}
code_bg = f"import sys; sys.path.insert(0, r'{ROOT}'); from app import config; print(config.get_library_root())"
r = subprocess.run([sys.executable, "-c", code_bg], env=env_bg, capture_output=True, text=True)
check("PHOTAG_BACKGROUND=1 (backup program): the library is NOT moved", (p / "PhotoManager" / "catalog.db").exists() and not (p / "Photag").exists() and r.stdout.strip() == str(p / "PhotoManager"), r.stdout.strip())
out = run(p, "print(config.get_library_root())")
check("...and the real app still performs the requested move at its next start", out == str(p / "Photag"))
shutil.rmtree(p, ignore_errors=True)

# ---- F: the pointer names a library elsewhere: an old PhotoManager folder is not touched
p = profile(); legacy_library(p)
elsewhere = p / "D_drive_library"; elsewhere.mkdir()
(p / "AppData" / "photag").mkdir(parents=True)
(p / "AppData" / "photag" / "config.json").write_text(json.dumps({"library_root": str(elsewhere)}), "utf-8")
run(p, "config.request_legacy_move()")
out = run(p, "print(config.get_library_root())")
check("a library chosen elsewhere stays where the user put it", out == str(elsewhere) and (p / "PhotoManager" / "catalog.db").exists())
shutil.rmtree(p, ignore_errors=True)

print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
