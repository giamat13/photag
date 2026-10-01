"""Tests for app/security.py: the local server only serves photag's own window.

    py -3.12 tools/test_server_security.py
"""
import http.client
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8777
tmp = Path(tempfile.mkdtemp(prefix="photag_sec_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"), "PYTHONIOENCODING": "utf-8"}
for d in ("appdata", "local", "home"):
    (tmp / d).mkdir()
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def req(method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=20)
    h = {"Host": f"127.0.0.1:{PORT}", **(headers or {})}
    if body is not None:
        h.setdefault("Content-Type", "application/json")
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    OWN = f"http://127.0.0.1:{PORT}"
    check("normal request (Host 127.0.0.1)", req("GET", "/api/status")[0] == 200)
    check("Host: localhost works too", req("GET", "/api/status", {"Host": f"localhost:{PORT}"})[0] == 200)
    check("Host: [::1] works too", req("GET", "/api/status", {"Host": f"[::1]:{PORT}"})[0] == 200)
    check("a foreign Host name (DNS rebinding) is refused", req("GET", "/api/status", {"Host": f"evil.example:{PORT}"})[0] == 400)
    check("a foreign Host also refuses the UI files", req("GET", "/", {"Host": "evil.example"})[0] == 400)
    check("no Origin (scheduled task, curl, scripts) still works for changes", req("POST", "/api/library/move-ack", body="{}")[0] == 200)
    check("POST from our own origin works", req("POST", "/api/library/move-ack", {"Origin": OWN}, "{}")[0] == 200)
    check("POST from another website is refused", req("POST", "/api/library/move-ack", {"Origin": "http://evil.example"}, "{}")[0] == 403)
    check("POST from another website that also uses localhost:another port is refused", req("POST", "/api/library/move-ack", {"Origin": "http://localhost:9999"}, "{}")[0] == 403)
    check("DELETE from another website is refused", req("DELETE", "/api/backup/photag-20200101-000000-manual.zip", {"Origin": "http://evil.example"})[0] == 403)
    check("PATCH from another website is refused", req("PATCH", "/api/photos", {"Origin": "http://evil.example"}, '{"ids":[1],"rating":5}')[0] == 403)
    check("Origin: null (sandboxed pages) is refused", req("POST", "/api/library/move-ack", {"Origin": "null"}, "{}")[0] == 403)
    check("the browser's own cross-site marker on a change is refused", req("POST", "/api/library/move-ack", {"Sec-Fetch-Site": "cross-site"}, "{}")[0] == 403)
    check("cross-site reading of the API is refused", req("GET", "/api/status", {"Sec-Fetch-Site": "cross-site"})[0] == 403)
    check("same-origin fetches from the window work", req("GET", "/api/status", {"Sec-Fetch-Site": "same-origin"})[0] == 200)
    check("the window itself (no Sec-Fetch-Site on navigation to /) works", req("GET", "/", {"Sec-Fetch-Site": "none"})[0] == 200)
finally:
    srv.terminate()
    try:
        srv.wait(timeout=15)
    except Exception:
        srv.kill()
    shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
