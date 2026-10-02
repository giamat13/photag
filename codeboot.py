"""Code updates without running a new installer.

photag.exe contains its own copy of the `app` package (the program's Python code and its web UI). A *code update* puts
a newer copy of that package in <install folder>\\code\\app; at start-up `activate()` makes `import app...` prefer it.
Nothing executable is replaced, so Windows features that block unknown or unsigned programs (Smart App Control) have
nothing new to block: photag.exe itself stays byte-for-byte the same file that is already allowed to run.

Rules that keep it safe:
  * a code update only applies to the exe it was made for: RUNTIME below is bumped whenever the libraries or the
    packaging change (tools/test_runtime_lock.py fails the build if that was forgotten); such releases come as a normal
    installer, and the installer deletes any old code folder;
  * every start counts itself in code\\.boots and the app confirms a good start (confirm()); three starts in a row
    that never got that far put the previous code (code.prev) back, or the built-in copy when there is none;
  * a broken override that cannot even be imported is dropped the same way (rollback()).

This module must stay tiny and use only the standard library: it runs before anything else is imported.
"""
import importlib.machinery
import json
import os
import shutil
import sys
from pathlib import Path

RUNTIME = 1                 # see the rules above; also written into every code zip's manifest.json
MAX_BOOTS = 3

_active: Path | None = None


def install_dir() -> Path | None:
    env = os.environ.get("PHOTAG_CODE_BASE")            # tests
    if env:
        return Path(env)
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else None


def _read_manifest(code: Path) -> dict | None:
    try:
        m = json.loads((code / "manifest.json").read_text("utf-8"))
        if m.get("runtime") == RUNTIME and (code / "app" / "__init__.py").is_file():
            return m
    except Exception:
        pass
    return None


class _CodeFirst:
    """Import hook: modules of the `app` package come from the code folder before the copy inside the exe."""

    def __init__(self, code: Path):
        self.code = code

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "app":
            return importlib.machinery.PathFinder.find_spec(fullname, [str(self.code)])
        if fullname.startswith("app."):
            return importlib.machinery.PathFinder.find_spec(fullname, path)
        return None


def activate(base: Path | None = None, count: bool = True) -> str:
    """Call before `import app`. Returns what happened: 'builtin', 'override', 'rolled-back' or 'ignored'.
    count=False for headless runs (the backup task): they use the code but never count as a start of the app."""
    global _active
    base = base or install_dir()
    if base is None:
        return "builtin"
    code = base / "code"
    if not code.is_dir():
        return "builtin"
    result = "override"
    boots = 0
    try:
        boots = int((code / ".boots").read_text() or 0)
    except Exception:
        pass
    if boots >= MAX_BOOTS:                                # it never got as far as a confirmed start: put the old code back
        rollback(base)
        result = "rolled-back"
        code = base / "code"
        if not code.is_dir():
            return result
    m = _read_manifest(code)
    if m is None:
        return "ignored" if result == "override" else result
    if count:
        try:
            (code / ".boots").write_text(str(boots + 1) if result == "override" else "1")
        except OSError:
            pass
    sys.meta_path.insert(0, _CodeFirst(code))
    _active = code
    return result


def active() -> Path | None:
    return _active


def confirm():
    """The app started properly (its server is listening): the code is good."""
    if _active is not None:
        try:
            (_active / ".boots").write_text("0")
        except OSError:
            pass


def rollback(base: Path | None = None) -> str:
    """Drop the override that does not work: the previous code if there is one, else the built-in copy."""
    base = base or install_dir()
    if base is None:
        return "builtin"
    code, prev, bad = base / "code", base / "code.prev", base / "code.bad"
    shutil.rmtree(bad, ignore_errors=True)
    try:
        if code.exists():
            os.replace(code, bad)
        if prev.is_dir():
            os.replace(prev, code)
            try:
                (code / ".boots").write_text("0")
            except OSError:
                pass
            return "previous"
    except OSError:
        shutil.rmtree(code, ignore_errors=True)
    return "builtin"
