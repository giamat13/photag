"""Tests for the interrupted-update safety net (app/updater.py): the whole program FOLDER is saved before the installer
runs and put back by recover.ps1 (the real PowerShell script) when the update did not finish.

    py -3.12 tools/test_update_rollback.py
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
root = Path(tempfile.mkdtemp(prefix="photag_rollback_test_"))
state, app = root / "state", root / "app"
(app / "_internal" / "sub").mkdir(parents=True); state.mkdir()
exe = app / "photag.exe"
files = {"photag.exe": b"MZ-old-exe" * 5000, "_internal/a.dll": os.urandom(30000), "_internal/sub/b.pyd": os.urandom(20000),
         "_internal/c.txt": b"hello", "LICENSE": b"GPL text", "unins000.exe": b"uninstaller-v1", "unins000.dat": b"uninstall-data-v1"}
for rel, data in files.items():
    (app / rel).write_bytes(data)
os.environ.update(PHOTAG_UPDATE_STATE_DIR=str(state), PHOTAG_UPDATE_FAKE_EXE=str(exe), PHOTAG_UPDATE_DRY_RUN="1",
                  LOCALAPPDATA=str(root / "la"), APPDATA=str(root / "ad"))
sys.path.insert(0, str(ROOT))
from app import updater

res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def tree(d: Path, skip_unins=True) -> dict:
    return {p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*"))
            if p.is_file() and not (skip_unins and p.name.lower().startswith("unins"))}


def recover(*extra):
    return subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(state / "recover.ps1"),
                           "-StateDir", str(state), "-NoLaunch", *extra], capture_output=True, text=True, timeout=180)


def start_update(version="1.2.0"):
    inst = updater.download_dir() / f"photagSetup-{version}.exe"
    inst.write_bytes(b"MZ-installer")
    return updater.launch(str(inst))


def age_journal():
    j = json.loads((state / "pending.json").read_text("utf-8")); j["started"] = time.time() - 3600
    (state / "pending.json").write_text(json.dumps(j))


original = tree(app)
unins_before = {k: (app / k).read_bytes() for k in ("unins000.exe", "unins000.dat")}

# ---- 1. the folder is saved before the installer starts
r = start_update()
j = json.loads((state / "pending.json").read_text("utf-8"))
saved = tree(Path(j["backup_dir"]), skip_unins=False)
check("launch (dry run) saves the whole program folder", r["mode"] == "dry-run" and saved == original, f"{len(saved)} files")
check("the uninstaller files are not part of the saved copy", not (Path(j["backup_dir"]) / "unins000.exe").exists())
check("journal knows the folder, the saved copy and the fingerprint", Path(j["app_dir"]) == app and j["fingerprint"] and Path(j["backup_dir"]).is_dir())

# ---- 2. nothing changed + an old journal: nothing to restore, nothing copied
age_journal()
check("update never really started (folder unchanged) -> failed_intact, journal cleared", updater.reconcile()["state"] == "failed_intact" and not (state / "pending.json").exists())
updater.ack_notice()

# ---- 3. an update that was cut in the middle
start_update()
(app / "photag.exe").write_bytes(b"MZ-half-written-new-exe")               # replaced
(app / "_internal" / "a.dll").unlink()                                       # deleted by the installer's cleanup
(app / "_internal" / "sub" / "b.pyd").write_bytes(b"truncated")              # cut off
(app / "_internal" / "new.dll").write_bytes(b"added by the new version")     # a file the old version never had
(app / "unins000.exe").write_bytes(b"uninstaller-v2")                        # the installer updates its uninstaller
(app / "unins000.dat").write_bytes(b"uninstall-data-v2")
age_journal()
check("old journal, no flag, folder changed -> restoring", updater.reconcile()["state"] == "restoring")
p = recover()
check("recover.ps1 puts every file back byte for byte", p.returncode == 0 and tree(app) == original, p.stderr[:200] or (set(tree(app)) ^ set(original)))
check("a file the new version had added is removed again", not (app / "_internal" / "new.dll").exists())
check("the uninstaller files were not touched by the restore", (app / "unins000.exe").read_bytes() == b"uninstaller-v2")
check("journal cleared and the log says restored", not (state / "pending.json").exists() and "restored" in (state / "recovery.log").read_text("utf-8"))

# ---- 4. the installer finished (flag): the new version stays
start_update()
(app / "photag.exe").write_bytes(b"MZ-NEW-complete-exe")
(state / "done.flag").write_text("1.2.0")
p = recover()
check("flag present -> the new program is left alone", p.returncode == 0 and (app / "photag.exe").read_bytes() == b"MZ-NEW-complete-exe" and (state / "pending.json").exists())
(state / "pending.json").unlink(); (state / "done.flag").unlink()
(app / "photag.exe").write_bytes(files["photag.exe"])

# ---- 5. rollback copy missing: reports it, touches nothing
start_update()
(app / "photag.exe").write_bytes(b"MZ-broken")
shutil.rmtree(state / "rollback")
p = recover()
check("no rollback copy -> exit 1 and the program folder is untouched", p.returncode == 1 and (app / "photag.exe").read_bytes() == b"MZ-broken")
(state / "pending.json").unlink(missing_ok=True)

# ---- 6. a refused update (the folder cannot be saved) never starts
(app / "photag.exe").write_bytes(files["photag.exe"])
os.environ["PHOTAG_UPDATE_FAKE_EXE"] = str(app / "missing.exe")
try:
    start_update(); ok = False
except updater.UpdateError:
    ok = True
check("no program to save -> the update is refused and no journal is left", ok and not (state / "pending.json").exists())

shutil.rmtree(root, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
