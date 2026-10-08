"""Test: "Open with photag" while photag is already running (background mode) with an older photag.exe -- the new program gives the
picture to the running one, and the window that launcher opens on the main page shows that picture (bug since 13.0.0).

    py -3.12 tools/test_viewer_handoff.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
PORT = 8797
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_handoff_"))
for d in ("a", "l", "h", "pics"):
    (tmp / d).mkdir()
env = {**os.environ, "APPDATA": str(tmp / "a"), "LOCALAPPDATA": str(tmp / "l"), "USERPROFILE": str(tmp / "h"), "HOME": str(tmp / "h"),
       "PYTHONIOENCODING": "utf-8", "PHOTAG_BASE_PORT": str(PORT)}
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def call(m, p, b=None):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=30).read() or b"{}")


def start_with(pic, extra_env=None):
    """What an older photag.exe does on "Open with": load photag's code with the picture on the command line (then it opens a window
    on the running program's main page)."""
    code = "import sys; sys.path.insert(0, sys.argv[2]); from app import viewer; print('HANDED', viewer.HANDED_OFF)"
    out = subprocess.run([sys.executable, "-c", code, str(pic), str(ROOT)], env={**env, **(extra_env or {})}, capture_output=True, text=True, timeout=60)
    return out.stdout + out.stderr


Image.new("RGB", (60, 40), (10, 120, 200)).save(tmp / "pics" / "a.jpg", "JPEG")
Image.new("RGB", (60, 40), (200, 120, 10)).save(tmp / "pics" / "b.jpg", "JPEG")
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(80):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    check("the running photag has no picture to show at first", call("GET", "/api/viewer/startup")["token"] is None)
    out = start_with(tmp / "pics" / "a.jpg")
    check("a photag started with a picture gives it to the one already running", "HANDED True" in out, out.strip()[-300:])
    tok = call("GET", "/api/viewer/startup")["token"]
    check("the main page of the running photag then gets that picture (and opens the viewer on it)", tok and call("GET", f"/api/viewer/{tok}/info")["name"] == "a.jpg", tok)
    check("...only once", call("GET", "/api/viewer/startup")["token"] is None)
    out = start_with(tmp / "pics" / "b.jpg")
    call("POST", "/api/viewer/open", {"path": str(tmp / "pics" / "b.jpg")})          # a newer launcher opens its own viewer window
    check("with a newer launcher (it opens the viewer itself) the main page does not show the picture again", call("GET", "/api/viewer/startup")["token"] is None)
    out = start_with(tmp / "pics" / "nothere.jpg")
    check("a file that is not there is not handed over", "HANDED False" in out, out.strip()[-200:])
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()
out = start_with(tmp / "pics" / "a.jpg")
check("when no photag is running, the picture stays with the new program (it shows it itself)", "HANDED False" in out, out.strip()[-200:])
n = res.count(False)
print(f"\n{len(res) - n}/{len(res)} passed")
sys.exit(1 if n else 0)
