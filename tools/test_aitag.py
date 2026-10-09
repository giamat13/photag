"""Test: AI tagging against a fake OpenAI-compatible server (what Ollama / LM Studio look like to photag). The tags of every
photo are saved and visible to other readers while the job still runs (not only when it ends), a stop keeps what was
done, and the job also works when it is started after the main program ended (window closed, icon next to the clock).
Throw-away profile, nothing real is touched.

    py -3.12 tools/test_aitag.py
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "tools" / "sandbox" / "photos"
tmp = Path(tempfile.mkdtemp(prefix="photag_aitag_test_"))
for k, v in (("APPDATA", "appdata"), ("LOCALAPPDATA", "local"), ("USERPROFILE", "home")):
    (tmp / v).mkdir()
    os.environ[k] = str(tmp / v)
os.environ.update(PYTHONIOENCODING="utf-8")
sys.path.insert(0, str(ROOT))
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


# ---- the fake model server: the first 4 answers are quick, the rest are slow
hits = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._send({"data": [{"id": "fake-vision", "created": 1}]})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        hits.append(time.time())
        n = len(hits)
        time.sleep(0.3 if n <= 4 else 3.0)
        self._send({"choices": [{"message": {"content": json.dumps({"keywords": [f"alpha{n}", "beta"]})}}]})


httpd = ThreadingHTTPServer(("127.0.0.1", 8791), H)
threading.Thread(target=httpd.serve_forever, daemon=True).start()

from app import aitag, config, db, importer  # noqa: E402
from app.config import PATHS  # noqa: E402

config.set_library_root(tmp / "lib")
PATHS.refresh()
db.init_db().close()
src = tmp / "src"
src.mkdir()
import shutil  # noqa: E402
for i, f in enumerate(sorted(SAMPLES.glob("*.jpg"))[:6]):
    shutil.copy2(f, src / f"p{i}.jpg")
importer.run_folder_import([str(f) for f in sorted(src.glob("*.jpg"))], [], None, False, importer.Progress())
n_photos = db.connect().execute("SELECT COUNT(*) FROM photos").fetchone()[0]
check("six photos imported for the test", n_photos == 6, n_photos)

aitag.save_settings("custom", "fake-vision", "en", "http://127.0.0.1:8791/v1", "ollama")


def ai_rows():
    c = db.connect()
    try:
        return c.execute("SELECT COUNT(DISTINCT photo_id) FROM photo_tags WHERE source='ai'").fetchone()[0]
    finally:
        c.close()


# ---- 1. tags show up while the job runs
prog = importer.Progress()
t = threading.Thread(target=aitag.run_aitag, args=(None, True, prog), daemon=True)
t.start()
seen_mid = 0
deadline = time.time() + 20
while time.time() < deadline and t.is_alive():
    n = ai_rows()
    if n and prog.state == "tagging":
        seen_mid = n
        break
    time.sleep(0.1)
check("the first photos' tags can be read by another connection while the job is still running", seen_mid >= 1, seen_mid)
t.join(60)
check("the job finishes", not t.is_alive() and prog.state == "done", prog.state)
check("all six photos are tagged at the end", ai_rows() == 6, ai_rows())

# ---- 2. stop keeps what was done
c = db.connect()
c.execute("DELETE FROM photo_tags WHERE source='ai'")
c.commit()
c.close()
hits.clear()
prog = importer.Progress()
t = threading.Thread(target=aitag.run_aitag, args=(None, True, prog), daemon=True)
t.start()
for _ in range(100):
    if ai_rows() >= 1:
        break
    time.sleep(0.1)
prog.cancel = True
t.join(60)
check("a stop ends the job", not t.is_alive())
check("...and keeps the tags that were already made (requests already sent are still saved)", ai_rows() >= 4, ai_rows())

# ---- 3. started after the main program ended (the window was closed, the icon keeps the program alive)
c = db.connect()
c.execute("DELETE FROM photo_tags WHERE source='ai'")
c.commit()
c.close()
hits.clear()
code = r"""
import atexit, sys
sys.path.insert(0, %r)
def late():
    from app import aitag, importer
    p = importer.Progress()
    aitag.run_aitag(None, True, p)
    print("STATE", p.state, getattr(p, "error", None) or getattr(p, "msg", ""))
atexit.register(late)
""" % str(ROOT)
r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120, env=os.environ.copy())
check("AI tagging started from an atexit handler (window closed) works", "STATE done" in r.stdout and ai_rows() == 6, (r.stdout + r.stderr).strip()[-300:])

httpd.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if all(res) else 1)
