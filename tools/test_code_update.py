"""Test: code updates (codeboot.py + app/updater.py) -- a newer copy of the `app` package is installed next to the exe
without any installer, is rolled back when it does not start, and refuses anything that is not plain code.

    py -3.12 tools/test_code_update.py
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
tmp = Path(tempfile.mkdtemp(prefix="photag_code_update_"))
install = tmp / "install"
install.mkdir()
(install / "photag.exe").write_bytes(b"MZ stand-in")
os.environ.update(PHOTAG_UPDATE_STATE_DIR=str(tmp / "state"), PHOTAG_UPDATE_FAKE_EXE=str(install / "photag.exe"), PHOTAG_UPDATE_DRY_RUN="1")
sys.path.insert(0, str(ROOT))
import codeboot  # noqa: E402
from app import updater  # noqa: E402
from app.version import __version__  # noqa: E402

res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def make_zip(path: Path, version: str, runtime=None, extra: dict | None = None) -> Path:
    runtime = codeboot.RUNTIME if runtime is None else runtime
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("manifest.json", json.dumps({"version": version, "runtime": runtime}))
        for f in sorted((ROOT / "app").rglob("*.py")):
            if f.name == "version.py":
                z.writestr("app/version.py", f'__version__ = "{version}"\nREPO = "giamat13/photag"\n')
            else:
                z.write(f, f.relative_to(ROOT).as_posix())
        for n, data in (extra or {}).items():
            z.writestr(n, data)
    return path


def run_boot(base: Path, count=True) -> tuple:
    """A fresh Python process: activate the override, then import app.version. Returns (result, version)."""
    code = ("import sys, pathlib; sys.path.insert(0, %r); import codeboot; r = codeboot.activate(pathlib.Path(%r), count=%r);"
            "import app.version as v; print(r, v.__version__)" % (str(ROOT), str(base), count))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    return tuple((out.stdout.strip() or "error error").split()[:2])


try:
    # ---- the zip made for a release
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "make_code_zip.py")], capture_output=True, text=True)
    z = ROOT / "dist" / f"photag-code-{__version__}-rt{codeboot.RUNTIME}.zip"
    check("make_code_zip builds photag-code-<version>-rt<runtime>.zip", z.is_file(), r.stderr[-200:])
    names = zipfile.ZipFile(z).namelist()
    check("it holds manifest.json, app/server.py and the web UI, and no executable files",
          "manifest.json" in names and "app/server.py" in names and "app/ui/app.js" in names
          and not any(n.lower().endswith((".exe", ".dll", ".pyd", ".pyc")) for n in names), len(names))
    z.unlink()

    # ---- applying a code update (no installer is run)
    d = updater.download_dir()
    z1 = make_zip(d / f"photag-code-9.0.1-rt{codeboot.RUNTIME}.zip", "9.0.1")
    r = updater.launch(str(z1))
    check("a code update is applied by launch(): code folder created, nothing launched",
          r["mode"] == "code-dry-run" and (install / "code" / "app" / "server.py").is_file(), r)
    check("the what-is-new notice for the restart was written", json.loads((updater.state_dir() / "just_updated.json").read_text())["to"] == "9.0.1")
    check("nothing but plain files was written (no exe / dll / pyd)", not any(p.suffix.lower() in (".exe", ".dll", ".pyd") for p in (install / "code").rglob("*")))
    check("photag.exe itself is untouched", (install / "photag.exe").read_bytes() == b"MZ stand-in")

    # ---- the override wins over the code that is built in
    res_, ver = run_boot(install)
    check("start with the downloaded code: the version comes from the code folder", (res_, ver) == ("override", "9.0.1"), (res_, ver))
    check("each start is counted", (install / "code" / ".boots").read_text() == "1")

    # ---- a second update keeps the previous code
    z2 = make_zip(d / f"photag-code-9.0.2-rt{codeboot.RUNTIME}.zip", "9.0.2")
    updater.launch(str(z2))
    check("a second update keeps the previous code as code.prev",
          (install / "code.prev" / "manifest.json").is_file() and json.loads((install / "code.prev" / "manifest.json").read_text())["version"] == "9.0.1")
    check("and the new code is used", run_boot(install, count=False)[1] == "9.0.2")

    # ---- three starts without a confirmed start: roll back to the previous code
    (install / "code" / ".boots").write_text("3")
    res_, ver = run_boot(install)
    check("three unconfirmed starts put the previous code back", (res_, ver) == ("rolled-back", "9.0.1"), (res_, ver))
    check("the bad code is kept aside as code.bad", (install / "code.bad").is_dir())
    # ---- and without any previous code: the built-in copy
    shutil.rmtree(install / "code.prev", ignore_errors=True)
    (install / "code" / ".boots").write_text("3")
    res_, ver = run_boot(install)
    check("with no previous code the built-in copy is used again", ver == __version__ and not (install / "code").exists(), (res_, ver))
    # ---- confirm() resets the counter
    updater.launch(str(make_zip(d / f"photag-code-9.1.0-rt{codeboot.RUNTIME}.zip", "9.1.0")))
    c = ("import sys, pathlib; sys.path.insert(0, %r); import codeboot; codeboot.activate(pathlib.Path(%r)); "
         "codeboot.confirm(); print(open(%r).read())" % (str(ROOT), str(install), str(install / "code" / ".boots")))
    out = subprocess.run([sys.executable, "-c", c], capture_output=True, text=True).stdout.strip()
    check("a confirmed start resets the counter", out == "0", out)
    check("headless runs (the backup task) use the code but are not counted as starts",
          run_boot(install, count=False)[1] == "9.1.0" and (install / "code" / ".boots").read_text() == "0")

    # ---- things that must be refused, leaving the installed code as it was
    before = (install / "code" / "manifest.json").read_text()

    def refused(name, zp, why):
        try:
            updater.launch(str(zp)); ok = False
        except updater.UpdateError as e:
            ok = True; why = f"{why}: {e}"
        check(f"refused: {name}", ok and (install / "code" / "manifest.json").read_text() == before and not (install / "code.new").exists(), why)

    refused("a code update made for another runtime", make_zip(d / "photag-code-9.2.0-rt2.zip", "9.2.0", runtime=2), "rt2")
    refused("a zip with an executable inside", make_zip(d / f"photag-code-9.2.1-rt{codeboot.RUNTIME}.zip", "9.2.1", extra={"app/evil.exe": b"MZ"}), "exe")
    refused("a zip with a file outside app/", make_zip(d / f"photag-code-9.2.2-rt{codeboot.RUNTIME}.zip", "9.2.2", extra={"other.txt": b"x"}), "outside")
    refused("a zip that tries to leave the folder", make_zip(d / f"photag-code-9.2.3-rt{codeboot.RUNTIME}.zip", "9.2.3", extra={"app/../../x.py": b"x=1"}), "..")
    refused("code with a syntax error", make_zip(d / f"photag-code-9.2.4-rt{codeboot.RUNTIME}.zip", "9.2.4", extra={"app/broken.py": b"def (:\n"}), "syntax")
    refused("a manifest that does not match the file name", make_zip(d / f"photag-code-9.2.5-rt{codeboot.RUNTIME}.zip", "9.9.9"), "manifest")

    # ---- which asset does a release offer
    base_url = updater.ALLOWED_DOWNLOAD + "v9.3.0/"
    rel_assets = [{"name": "photagSetup.exe", "browser_download_url": base_url + "photagSetup.exe", "size": 5, "digest": "sha256:" + "a" * 64},
                  {"name": f"photag-code-9.3.0-rt{codeboot.RUNTIME}.zip", "browser_download_url": base_url + f"photag-code-9.3.0-rt{codeboot.RUNTIME}.zip",
                   "size": 9, "digest": "sha256:" + "b" * 64},
                  {"name": "photag-code-9.3.0-rt99.zip", "browser_download_url": "x", "size": 9, "digest": "sha256:" + "c" * 64}]
    ca = updater._pick_code_asset(rel_assets, "9.3.0")
    check("a release's code zip for this runtime is found (and only that one)", ca and ca["name"].endswith(f"rt{codeboot.RUNTIME}.zip") and ca["sha256"] == "b" * 64, ca)
    check("a code zip for another version or without a digest is ignored",
          updater._pick_code_asset(rel_assets, "9.4.0") is None and updater._pick_code_asset([{**rel_assets[1], "digest": None}], "9.3.0") is None)
    check("can_install is true with only a code zip", updater.can_install({"asset": None, "code_asset": ca}))
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    for f in (ROOT / "dist").glob("photag-code-*.zip"):
        f.unlink(missing_ok=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
