"""Test: app/platform_dirs.py picks per-OS folders; Windows behaviour is unchanged and env vars always win."""
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import platform_dirs as pd  # noqa: E402

res = []
def check(n, ok, extra=""):
    res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n + (f"  [{extra}]" if extra else ""))

H = Path.home()
def run(plat, env):
    keep = {k: v for k, v in os.environ.items() if k not in ("APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_DATA_HOME")}
    keep.update(env)
    with mock.patch.object(sys, "platform", plat), mock.patch.dict(os.environ, keep, clear=True):
        return pd.roaming_base(), pd.local_base()

check("windows: APPDATA / LOCALAPPDATA", run("win32", {"APPDATA": "C:/R", "LOCALAPPDATA": "C:/L"}) == (Path("C:/R"), Path("C:/L")))
check("windows without the variables: home folder (as before)", run("win32", {}) == (H, H))
check("macOS: Application Support", run("darwin", {}) == (H / "Library" / "Application Support",) * 2)
check("linux: ~/.config and ~/.local/share", run("linux", {}) == (H / ".config", H / ".local" / "share"))
check("linux: XDG variables are honoured", run("linux", {"XDG_CONFIG_HOME": "/x/c", "XDG_DATA_HOME": "/x/d"}) == (Path("/x/c"), Path("/x/d")))
check("env vars win on every system", run("linux", {"APPDATA": "/a", "LOCALAPPDATA": "/l"}) == (Path("/a"), Path("/l")))
n = res.count(False); print(f"\n{len(res)-n}/{len(res)} passed"); sys.exit(1 if n else 0)
