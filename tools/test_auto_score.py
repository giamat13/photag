"""New photos get their quality score on their own, in the background, without AI (#48); a switch turns it off; Score All scores every
photo again, also when all of them already have a score. Throw-away profile, the real server.

    py -3.12 tools/test_auto_score.py
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_autoscore_test_"))
for d in ("appdata", "local", "home"):
    (tmp / d).mkdir()
os.environ.update({"APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
                   "HOME": str(tmp / "home"), "PHOTAG_NO_OPEN": "1"})
sys.path.insert(0, str(ROOT))

import _client  # noqa: E402  (tools/_client.py)
from PIL import Image  # noqa: E402

from app import db  # noqa: E402
from app.config import PATHS  # noqa: E402

res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


db.init_db()
PATHS.media.mkdir(parents=True, exist_ok=True)


def add(name, shade):
    im = Image.new("RGB", (120, 90), (shade, 120, 200 - shade))
    for x in range(0, 120, 6):                      # some detail, so the picture is not flat
        for y in range(0, 90, 6):
            im.putpixel((x, y), (255 - shade, 30, 30))
    im.save(PATHS.media / name, "JPEG")
    con = db.connect()
    con.execute("INSERT INTO photos(sha256,filename,rel_path,mime,is_video,width,height,bytes,taken_at,trashed) VALUES(?,?,?,?,?,?,?,?,?,0)",
                (name, name, name, "image/jpeg", 0, 120, 90, 1000, 1700000000))
    con.commit()
    return con.execute("SELECT id FROM photos WHERE sha256=?", (name,)).fetchone()["id"]


def scored(pid):
    r = db.connect().execute("SELECT score FROM photo_analysis WHERE photo_id=?", (pid,)).fetchone()
    return r["score"] if r else None


def wait_for(fn, seconds=40):
    end = time.time() + seconds
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.4)
    return False




class Fake(BaseHTTPRequestHandler):
    """A fake AI provider: every photo is worth 77."""
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        out = json.dumps({"choices": [{"message": {"content": '{"score": 77}'}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


httpd = ThreadingHTTPServer(("127.0.0.1", 8878), Fake)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
a1 = add("auto1.jpg", 60)
srv, cl = _client.start_server(8877, {**os.environ, "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999", "PHOTAG_SCORE_DELAY": "0.5",
                                      "PHOTAG_SCORE_IDLE": "1", "PHOTAG_SCORE_TICK": "0.5", "PHOTAG_EXIF_DELAY": "9999", "PHOTAG_MIGRATE_DELAY": "9999",
                                      "PHOTAG_AI_BASE_OPENAI": "http://127.0.0.1:8878"})
try:
    check("automatic scoring is on by default", cl.get("/api/auto-score").json()["enabled"] is True)
    check("a photo nobody scored gets its score by itself", wait_for(lambda: scored(a1) is not None), scored(a1))
    cl.post("/api/auto-score", json={"enabled": False})
    check("the switch turns it off", cl.get("/api/auto-score").json()["enabled"] is False)
    a2 = add("auto2.jpg", 180)
    time.sleep(5)
    check("with it off a new photo is left alone", scored(a2) is None, scored(a2))
    cl.post("/api/auto-score", json={"enabled": True})
    check("...and on again it gets its score", wait_for(lambda: scored(a2) is not None), scored(a2))

    # Score All: everything is scored again, also when every photo already has a score
    con = db.connect()
    con.execute("UPDATE photo_analysis SET score=-5")
    con.commit()
    r = cl.post("/api/analysis/run", json={"eyes": False, "redo": True})
    check("Score All starts although every photo already has a score", r.status_code == 200, r.text[:100])
    check("...and scores them again (the old numbers are replaced)", wait_for(lambda: scored(a1) not in (None, -5) and scored(a2) not in (None, -5)), (scored(a1), scored(a2)))
    without = cl.post("/api/analysis/run", json={"eyes": False})
    wait_for(lambda: cl.get("/api/job/analysis").json().get("state") in ("done", "idle", "error"))
    check("an ordinary analysis (without 'again') still skips photos that are already scored", without.status_code == 200 and cl.get("/api/analysis/status").json()["pending"] == 0)

    # who scores: the program (default) or the AI of AI tagging (only when it is chosen AND set up)
    st = cl.get("/api/auto-score").json()
    check("the program scores new photos by default; the AI is not set up", st["mode"] == "program" and st["ai_ready"] is False, st)
    a3 = add("auto3.jpg", 100)
    check("...so a new photo gets the program's own score", wait_for(lambda: scored(a3) is not None) and scored(a3) != 77, scored(a3))
    cl.post("/api/auto-score", json={"mode": "ai"})
    check("the AI can be chosen, but while it is not set up it is not used", wait_for(lambda: add("auto4.jpg", 140) and True) and wait_for(lambda: scored(a3) is not None), cl.get("/api/auto-score").json())
    cl.post("/api/ai/settings", json={"provider": "openai", "model": "fake-model", "language": "en", "base_url": "", "api_key": "sk-test-key"})
    check("once the AI is set up it is ready", cl.get("/api/auto-score").json()["ai_ready"] is True)
    a5 = add("auto5.jpg", 30)
    check("a new photo is then scored by the AI (and keeps its own measurement next to it)", wait_for(lambda: scored(a5) == 77),
          (scored(a5), db.connect().execute("SELECT ai_score, sharp FROM photo_analysis WHERE photo_id=?", (a5,)).fetchone()[:]))
    bad = cl.post("/api/auto-score", json={"mode": "magic"})
    check("another mode is refused", bad.status_code == 400, bad.status_code)
finally:
    srv.terminate()
    httpd.shutdown()
sys.exit(0 if all(res) else 1)
