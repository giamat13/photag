"""Test: app/fileassoc.py - the "Open with photag" registration (plan everywhere; the real registry on Windows, in test keys)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import fileassoc  # noqa: E402

res = []
def check(n, ok, extra=""):
    res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n + (f"  [{extra}]" if extra else ""))

EXE = r"C:\Users\me\AppData\Local\Programs\photag\photag.exe"
rows = fileassoc.plan(EXE)
d = {(k, n): v for k, n, v, _ in rows}
check("open command runs the exe with the picture", d[(r"Software\Classes\photag.Image\shell\open\command", "")] == f'"{EXE}" "%1"')
check("every type is in OpenWithProgids", all((rf"Software\Classes\{e}\OpenWithProgids", "photag.Image") in d for e in fileassoc.EXTS))
check("every type is in Capabilities", all(d[(r"Software\photag\Capabilities\FileAssociations", e)] == "photag.Image" for e in fileassoc.EXTS))
check("registered application points at Capabilities", d[(r"Software\RegisteredApplications", "photag")] == r"Software\photag\Capabilities")
check("jpg png heic tiff dng covered", {".jpg", ".png", ".heic", ".tiff", ".dng"} <= set(fileassoc.EXTS))
check("nothing outside HKCU subtrees photag owns or the OpenWith lists", all(k.startswith(("Software\\Classes\\", "Software\\photag", "Software\\RegisteredApplications")) for k, *_ in rows))
check("test roots are used", all(k.startswith("Software\\photag-test") for k, *_ in fileassoc.plan(EXE, r"Software\photag-test\Classes", r"Software\photag-test\Soft")))

if sys.platform == "win32":
    import winreg
    cl, sw = r"Software\photag-test-fa\Classes", r"Software\photag-test-fa\Soft"
    try:
        fileassoc.register(EXE, cl, sw)
        check("registered", fileassoc.is_registered(EXE, cl))
        check("not registered for another exe", not fileassoc.is_registered(r"C:\other\photag.exe", cl))
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{cl}\.jpg\OpenWithProgids") as k:
            check("jpg lists photag.Image", winreg.QueryValueEx(k, "photag.Image")[0] in (b"", None))
        fileassoc.unregister(EXE, cl, sw)
        check("unregistered", not fileassoc.is_registered(EXE, cl))
    finally:
        for root in (r"Software\photag-test-fa",):
            fileassoc._delete_tree(root)
# ---- on by default, off when the user switched it off; the picture named on the command line (older launcher) ----
import os, subprocess, tempfile
from PIL import Image
tmp = Path(tempfile.mkdtemp(prefix="photag_fa_"))
pic = tmp / "x.jpg"; Image.new("RGB", (8, 8)).save(pic)
env = {**os.environ, "APPDATA": str(tmp / "a"), "LOCALAPPDATA": str(tmp / "l"), "HOME": str(tmp / "h"), "USERPROFILE": str(tmp / "h"), "PYTHONIOENCODING": "utf-8"}
env.pop("PHOTAG_FILEASSOC_EXE", None)
def run(code, argv=()):
    r = subprocess.run([sys.executable, "-c", "import sys; sys.argv=['photag.exe']+sys.argv[1:]\n" + code, *argv], cwd=str(Path(__file__).resolve().parent.parent), env=env, capture_output=True, text=True)
    return r.stdout.strip()
check("ensure does nothing when not packaged", run("from app import fileassoc; print(fileassoc.ensure())") == "False")
check("opt-out marker is remembered", run("from app import fileassoc as f; f.set_opted_out(True); print(f.opted_out())") == "True")
check("opt-out can be cleared", run("from app import fileassoc as f; f.set_opted_out(False); print(f.opted_out())") == "False")
check("ensure respects the opt-out", run("import os; os.environ['PHOTAG_FILEASSOC_EXE']=r'C:\\p\\photag.exe'\nfrom app import fileassoc as f; f.set_opted_out(True); f.register=lambda *a, **k: 1/0; print(f.ensure())") == "False")
check("started with a picture: a token once, then none", run("from app import viewer; a=viewer.startup_token(); b=viewer.startup_token(); print(bool(a), b)", [str(pic)]) == "True None")
check("started without a picture: none", run("from app import viewer; print(viewer.startup_token())") == "None")
check("a text file on the command line is ignored", run("from app import viewer; print(viewer.startup_token())", [str(tmp / "n.txt")]) == "None")
import shutil; shutil.rmtree(tmp, ignore_errors=True)

n = res.count(False); print(f"\n{len(res)-n}/{len(res)} passed"); sys.exit(1 if n else 0)
