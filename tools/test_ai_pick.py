"""The AI picks the best photo in Rank / Compare (#45): it uses the provider, key and model of AI tagging (here a fake provider on this
computer), sends the photos together, and the "Rank and Compare" setting is ask / always / never (ask when the AI is set up, else off).
Throw-away profile, the real server.

    py -3.12 tools/test_ai_pick.py
"""
import io
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="photag_aipick_test_"))
for d in ("appdata", "local", "home"):
    (tmp / d).mkdir()
os.environ.update({"APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
                   "HOME": str(tmp / "home"), "PHOTAG_NO_OPEN": "1"})
sys.path.insert(0, str(ROOT))

import _client  # noqa: E402  (tools/_client.py)
from PIL import Image  # noqa: E402

from app import db, images  # noqa: E402

APP, FAKE = 8875, 8876
res, SEEN = [], []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        SEEN.append((self.path, body))
        out = json.dumps({"choices": [{"message": {"content": '```json\n{"best": 2, "reason": "Sharper, and everybody has their eyes open"}\n```'}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


httpd = ThreadingHTTPServer(("127.0.0.1", FAKE), Fake)
threading.Thread(target=httpd.serve_forever, daemon=True).start()

db.init_db()
con = db.connect()
ids = []
for n in range(3):
    sha = f"aipick{n}"
    con.execute("INSERT INTO photos(sha256,filename,rel_path,mime,is_video,width,height,bytes,taken_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (sha, sha + ".jpg", sha + ".jpg", "image/jpeg", 0, 40, 30, 1, 1700000000 + n))
    ids.append(con.execute("SELECT id FROM photos WHERE sha256=?", (sha,)).fetchone()["id"])
    tp = images.thumb_path(sha)
    tp.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (40 * n, 90, 200 - 40 * n)).save(buf, "JPEG")
    tp.write_bytes(buf.getvalue())
con.commit()

srv, cl = _client.start_server(APP, {**os.environ, "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999",
                                     "PHOTAG_AI_BASE_OPENAI": f"http://127.0.0.1:{FAKE}"})
try:
    s = cl.get("/api/ai/settings").json()
    check("the AI is not set up and the setting is off by default", s["configured"] is False and s["rank_mode"] == "off", (s["configured"], s["rank_mode"]))
    r = cl.post("/api/ai/settings", json={"provider": "openai", "model": "fake-model", "language": "en", "base_url": "", "api_key": "sk-test-key"})
    s = cl.get("/api/ai/settings").json()
    check("once the AI is set up (the same settings as AI tagging) the default is: ask every time", r.status_code == 200 and s["configured"] is True and s["rank_mode"] == "ask", (r.status_code, s["rank_mode"]))
    check("the setting can be 'always'", cl.post("/api/ai/rank-mode", json={"mode": "always"}).json()["mode"] == "always" and cl.get("/api/ai/settings").json()["rank_mode"] == "always")
    check("...and 'off'", cl.post("/api/ai/rank-mode", json={"mode": "off"}).json()["mode"] == "off")
    bad = cl.post("/api/ai/rank-mode", json={"mode": "sometimes"})
    check("another value is refused", bad.status_code == 400, bad.status_code)
    cl.post("/api/ai/settings", json={"provider": "openai", "model": "fake-model", "language": "en", "base_url": "", "api_key": ""})
    check("saving the AI settings again keeps the choice", cl.get("/api/ai/settings").json()["rank_mode"] == "off")

    r = cl.post("/api/ai/pick", json={"ids": ids})
    j = r.json()
    check("the AI picks the second photo and says why", r.status_code == 200 and j["best_id"] == ids[1] and "eyes open" in j["reason"] and j["model"] == "fake-model", r.text[:200])
    path, body = SEEN[-1]
    content = body["messages"][0]["content"]
    check("all three photos went to the AI in one request, in order, with the model of AI tagging",
          path.endswith("/chat/completions") and body["model"] == "fake-model" and sum(1 for c in content if c["type"] == "image_url") == 3, (path, body["model"]))
    check("one photo is not enough", cl.post("/api/ai/pick", json={"ids": [ids[0]]}).status_code == 400)
    check("no photo changed: the AI only answers, nothing is flagged or tagged",
          db.connect().execute("SELECT COUNT(*) FROM photo_tags").fetchone()[0] == 0 and db.connect().execute("SELECT SUM(ABS(flag)) FROM photos").fetchone()[0] == 0)
finally:
    srv.terminate()
    httpd.shutdown()
sys.exit(0 if all(res) else 1)
