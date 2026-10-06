"""Test: running photag from source on Linux / macOS -- no window toolkit means the browser is used; the updater never offers the
Windows installer off Windows; photag.py does not touch ctypes.windll off Windows."""
import ast
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_unixrun_"))
for k, v in (("APPDATA", "a"), ("LOCALAPPDATA", "l"), ("USERPROFILE", "h"), ("HOME", "h")):
    (tmp / v).mkdir(exist_ok=True)
    os.environ[k] = str(tmp / v)
sys.path.insert(0, str(ROOT))
res = []


def check(n, ok, extra=""):
    res.append(bool(ok)); print(("PASS " if ok else "FAIL ") + n + (f"  [{extra}]" if extra else ""))


src = (ROOT / "photag.py").read_text(encoding="utf-8")
tree = ast.parse(src)
check("photag.py has the browser fallback for a missing window toolkit (not on Windows)", "_browser_mode" in src and 'sys.platform != "win32"' in src)

if sys.platform.startswith("win"):
    print("SKIP the rest: it checks what happens off Windows")
    n = res.count(False); print(f"\n{len(res)-n}/{len(res)} passed"); sys.exit(1 if n else 0)

# the updater: a release with a Windows installer and a code update
from app import updater, config  # noqa: E402
rel = {"tag_name": "v99.0.0", "html_url": "https://example.invalid/r", "body": "x", "published_at": "2026-01-01T00:00:00Z",
       "assets": [{"name": "photagSetup.exe", "browser_download_url": "https://github.com/giamat13/photag/releases/download/v99.0.0/photagSetup.exe", "size": 5,
                   "digest": "sha256:" + "a" * 64}]}
updater._cache.update(at=time.time(), data=rel, beta=config.get_beta_channel())
info = updater.check()
check("off Windows: a newer release is reported but the .exe installer is not offered", info["available"] and info["asset"] is None and not updater.can_install(info), info.get("asset"))
os.environ["PHOTAG_UPDATE_ANY_PLATFORM"] = "1"
info = updater.check()
check("(the test switch brings the installer back, for the Windows-style tests)", info["asset"] is not None)
os.environ.pop("PHOTAG_UPDATE_ANY_PLATFORM")

# a real start of photag.py with no window toolkit: the server answers and it says where
port = 18000 + os.getpid() % 1000
env = {**os.environ, "PHOTAG_PORT": str(port), "PHOTAG_NO_WEBVIEW_TEST": "1", "BROWSER": "true", "PYTHONIOENCODING": "utf-8"}
code = ("import sys, types; sys.modules['webview'] = None\n"      # `import webview` raises ImportError: no toolkit
        "import runpy; runpy.run_path(%r, run_name='__main__')" % str(ROOT / "photag.py"))
import threading  # noqa: E402
p = subprocess.Popen([sys.executable, "-c", code], env=env, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
lines = []
threading.Thread(target=lambda: [lines.append(l) for l in p.stdout], daemon=True).start()
ok = False
url = None
for _ in range(120):
    time.sleep(0.5)
    if p.poll() is not None:
        break
    for cand in range(port, port + 6):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{cand}/api/status", timeout=1)
            ok, url = True, cand
            break
        except Exception:
            pass
    if ok:
        break
alive = p.poll() is None
check("photag.py without a window toolkit keeps running and serves the app (browser mode)", ok and alive, f"port {url}, alive={alive}")
for _ in range(40):                      # the server answers a moment before the main thread prints the address
    if any("photag is running at http://" in l for l in lines):
        break
    time.sleep(0.25)
p.terminate()
try:
    p.wait(timeout=10)
except Exception:
    p.kill()
out = "".join(lines)
check("it tells you the address", "photag is running at http://" in out, out[-200:])
n = res.count(False); print(f"\n{len(res)-n}/{len(res)} passed"); sys.exit(1 if n else 0)
