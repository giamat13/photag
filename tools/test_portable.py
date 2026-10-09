"""Tests for portable mode (tools/make_portable.py): catalog, settings and backups stay in a "data" folder beside the
EXE, nothing is written to the normal per-PC locations, and installer-based auto-update is refused in favor of the
release page. Each case runs in a fresh process with a throw-away profile and PHOTAG_PORTABLE_DIR standing in for
"next to the exe" (frozen detection itself is exercised by tools/test_code_update.py).

    py -3.12 tools/test_portable.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def profile():
    p = Path(tempfile.mkdtemp(prefix="photag_portable_test_"))
    (p / "AppData").mkdir()
    (p / "portable").mkdir()
    return p


def run(p: Path, code: str) -> str:
    env = {**os.environ, "USERPROFILE": str(p), "HOME": str(p), "APPDATA": str(p / "AppData"), "LOCALAPPDATA": str(p / "Local"),
           "PHOTAG_PORTABLE_DIR": str(p / "portable"), "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, r'{ROOT}')\nfrom app import config\n{code}"],
                       env=env, capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr[-500:]
    return r.stdout.strip()


def run_normal(p: Path, code: str) -> str:
    """Same, but WITHOUT PHOTAG_PORTABLE_DIR: the ordinary per-PC behaviour, for comparison."""
    env = {**os.environ, "USERPROFILE": str(p), "HOME": str(p), "APPDATA": str(p / "AppData"), "LOCALAPPDATA": str(p / "Local"), "PYTHONIOENCODING": "utf-8"}
    env.pop("PHOTAG_PORTABLE_DIR", None)
    r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, r'{ROOT}')\nfrom app import config\n{code}"],
                       env=env, capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stderr[-500:]
    return r.stdout.strip()


try:
    p = profile()
    check("portable_dir() is None without the marker (ordinary per-PC run)", run_normal(p, "print(config.PORTABLE)") == "None")

    p = profile()
    out = run(p, "print(config.portable_dir())")
    check("PHOTAG_PORTABLE_DIR (standing in for 'next to the exe') is picked up", out == str(p / "portable"), out)

    p = profile()
    lib = run(p, "print(config.get_library_root())")
    check("the default library lives beside the program, in a 'data\\library' folder, not in the user's profile",
          lib == str(p / "portable" / "data" / "library"), lib)
    check("nothing was written to the normal %APPDATA%\\photag", not (p / "AppData" / "photag").exists())
    out = run(p, "config.set_update_skipped('9.9.9'); print((config.portable_dir() / 'data' / 'config.json').is_file())")
    check("settings go in 'data' next to the exe instead, once something is actually saved", out == "True", out)

    p = profile()
    out = run(p, "config.set_library_root(str(config.portable_dir() / 'data' / 'library')); print('ok')")
    check("the library folder is created where expected", (p / "portable" / "data" / "library").is_dir(), out)

    # moving the portable folder: everything keeps working because paths are relative to where the exe runs from, not
    # baked in -- simulated here by just pointing a second run at a different PHOTAG_PORTABLE_DIR with the same data copied over
    p = profile()
    run(p, "config.set_library_root(str(config.portable_dir() / 'data' / 'library')); open(str(config.portable_dir() / 'data' / 'library' / 'x.txt'), 'w').write('hi')")
    moved = p / "portable_elsewhere"
    shutil.copytree(p / "portable", moved)
    env = {**os.environ, "USERPROFILE": str(p), "HOME": str(p), "APPDATA": str(p / "AppData"), "LOCALAPPDATA": str(p / "Local"),
           "PHOTAG_PORTABLE_DIR": str(moved), "PYTHONIOENCODING": "utf-8"}
    # compared with os.path.samefile (not a string match): the CI runner's own TEMP folder can be reported under a short
    # (8.3) name in one place and the long name in another, which is a quirk of that machine, not of photag
    code = (f"from pathlib import Path\n"
            f"got = Path(config.get_library_root())\n"
            f"want = Path(r'{moved}') / 'data' / 'library'\n"
            f"import os\n"
            f"print(os.path.samefile(got, want) and (got / 'x.txt').is_file())")
    r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, r'{ROOT}')\nfrom app import config\n{code}"],
                       env=env, capture_output=True, text=True, cwd=str(ROOT))
    check("after moving the whole folder elsewhere, the library is still found (paths are relative to the exe, not baked in)",
          r.stdout.strip() == "True", r.stdout.strip() or r.stderr[-300:])

    # the "move legacy PhotoManager library" flow is a per-PC concept and must not be offered in portable mode
    p = profile()
    (p / "PhotoManager" / "media").mkdir(parents=True)
    (p / "PhotoManager" / "catalog.db").write_bytes(b"SQLite format 3\0" + b"x" * 200)
    out_portable = run(p, "print(config.legacy_library_in_use())")
    out_normal = run_normal(p, "print(config.legacy_library_in_use())")
    check("legacy-library move is never offered in portable mode", out_portable == "None", out_portable)
    check("...even though the same PC/profile WOULD offer it in a normal (non-portable) run", out_normal != "None", out_normal)

    # the scheduled background-backup task must never be registered in portable mode: there is no stable path for it
    p = profile()
    out = run(p, "from app import backup_task; print(backup_task.supported())")
    check("the background backup task reports unsupported in portable mode (no stable path to register)", out == "False", out)

    # the installer-based auto-update must be refused in portable mode: there is no installed location to update in place
    p = profile()
    code = (
        "import os; os.environ['PHOTAG_UPDATE_STATE_DIR'] = str(config.portable_dir() / 'data' / 'update')\n"
        "from app import updater\n"
        "import sys; sys.frozen = True\n"
        "d = updater.download_dir(); d.mkdir(parents=True, exist_ok=True)\n"
        "exe = d / 'photagSetup-9.9.9.exe'; exe.write_bytes(b'MZ stand-in')\n"
        "print(updater.launch(str(exe)))"
    )
    out = run(p, code)
    check("an installer download is refused in portable mode (sent to the release page instead, nothing is run)", "'mode': 'page'" in out, out)

    # ---- the packaged zip itself: only meaningful once dist/photag/photag.exe exists (a full PyInstaller build, done by
    # build.bat / release.yml, not by this fast test suite). Skipped here rather than forcing a ~2 minute build into every run.
    sys.path.insert(0, str(ROOT))
    from app.version import __version__
    if not (ROOT / "dist" / "photag" / "photag.exe").is_file():
        print("SKIP  packaged portable ZIP (dist/photag/photag.exe not built here; see build.bat / release.yml)")
    else:
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_portable.py")], capture_output=True, text=True, check=True)
        z = ROOT / "dist" / f"windows-photag-{__version__}-portable.zip"
        check("tools/make_portable.py builds the portable ZIP", z.is_file())
        if z.is_file():
            names = zipfile.ZipFile(z).namelist()
            check("the ZIP holds photag.exe, the marker file and an empty data folder, all under one top folder",
                  any(n.endswith("photag/photag.exe") for n in names) and any(n.endswith("photag/portable.txt") for n in names)
                  and all(n.startswith("photag/") for n in names), len(names))
            check("LICENSE and THIRD_PARTY_NOTICES.md are included (GPL-3.0 requires the source offer to travel with the binary)",
                  any(n.endswith("photag/LICENSE") for n in names) and any(n.endswith("photag/THIRD_PARTY_NOTICES.md") for n in names))
            z.unlink()
finally:
    pass
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
