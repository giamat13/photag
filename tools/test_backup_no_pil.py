"""Test: app.backup imports and runs a plain (non-reduced) backup without PIL being importable at all --
the real condition inside photag-backup.exe, whose spec deliberately excludes PIL/numpy/etc to keep the
headless scheduled-backup program tiny and starting instantly (see photag_backup.spec, photag_backup.py).

Regression test for a real crash: app.backup used to import app.images (which imports PIL) at module
level, so merely importing app.backup -- which photag_backup.py does indirectly via backup_cli -- failed
with "ModuleNotFoundError: No module named 'PIL'" before any backup code ever ran. app.images is now only
imported inside the one function that actually needs it (reduced/compressed backups).

    py -3.12 tools/test_backup_no_pil.py
"""
import builtins
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_backup_no_pil_test_"))
for k, v in (("APPDATA", "a"), ("LOCALAPPDATA", "l"), ("USERPROFILE", "h"), ("HOME", "h")):
    (tmp / v).mkdir(exist_ok=True)
    os.environ[k] = str(tmp / v)
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, str(ROOT))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


for mod in list(sys.modules):
    if mod == "app" or mod.startswith("app."):
        del sys.modules[mod]

_real_import = builtins.__import__


def _no_pil_import(name, *a, **k):
    if name == "PIL" or name.startswith("PIL."):
        raise ModuleNotFoundError(f"No module named '{name}'")
    return _real_import(name, *a, **k)


builtins.__import__ = _no_pil_import
try:
    from app import backup  # must not raise, even though PIL is "not installed"
    check("app.backup imports cleanly with PIL unavailable", True)
except Exception as e:
    check("app.backup imports cleanly with PIL unavailable", False, f"{type(e).__name__}: {e}")
    backup = None

if backup is not None:
    from app import config, db  # noqa: E402
    from app.config import PATHS  # noqa: E402

    config.set_library_root(tmp / "lib")
    PATHS.refresh()
    db.init_db().close()
    backup.set_settings({"folder": str(tmp / "bk"), "include_media": True, "keep": 10})
    (PATHS.media / "2024").mkdir(parents=True, exist_ok=True)
    (PATHS.media / "2024" / "a.jpg").write_bytes(os.urandom(1000))
    try:
        s = backup.create_snapshot("manual")
        check("a plain backup (no compression) runs without PIL", s["media"]["copied"] == 1, s.get("media"))
    except ModuleNotFoundError as e:
        check("a plain backup (no compression) runs without PIL", False, str(e))

builtins.__import__ = _real_import
for mod in list(sys.modules):
    if mod == "app" or mod.startswith("app."):
        del sys.modules[mod]

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
